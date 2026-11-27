"""Syncs: a folder of the repository at a commit, mirrored into file-management (deployer spec D21).

    infra/syncs/<name>.yaml at that commit, reviewed in git like a service's deploy.yaml:
        sync: specs
        source: docs/specs                          # a folder in the repository
        destination: public/steven-universe/specs/  # <bucket>/<prefix>/ in file-management

A request names a sync and a commit, nothing else (as a deploy, D4): what is copied, and where, is
the manifest at that commit; the bytes come from the deployer's own clone, never from the request.

The mirror: every file under `source` is uploaded unless its object already has the same MD5 (an
unchanged file isn't written again: no new version); every object under `destination` that isn't in
`source` is deleted. The deployer's file-management grant is the limit of where a sync can write
(auth-service's policy: `public/steven-universe/` only), whatever a manifest says.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

import httpx
import yaml

from app.repo import Repo, RepoError

logger = logging.getLogger(__name__)

SYNC_RE = r"^[a-z][a-z0-9-]{1,62}$"
SHA_RE = r"^[0-9a-f]{40}$"
_SOURCE = re.compile(r"^[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*$")
_DESTINATION = re.compile(r"^[a-z0-9-]+/([A-Za-z0-9._-]+/)+$")
MAX_FILES = 2000                 # a sync is documents, not a dataset


class SyncError(Exception):
    """Refused before anything changed; `status` is the HTTP status."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass(frozen=True)
class Remote:
    key: str
    etag: str


class Files(Protocol):
    async def list(self, bucket: str, prefix: str) -> list[Remote]: ...
    async def put(self, bucket: str, key: str, body: bytes) -> None: ...
    async def delete(self, bucket: str, key: str) -> None: ...


class FileManagement:
    """file-management's /objects API, with the deployer's own token (audience file-management)."""

    def __init__(self, url: str, token, http: httpx.AsyncClient | None = None):
        self.url = url.rstrip("/")
        self.token = token                       # async () -> str
        self.http = http or httpx.AsyncClient(timeout=60)

    async def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {await self.token()}"}

    async def list(self, bucket: str, prefix: str) -> list[Remote]:
        out, cursor = [], None
        while True:
            params = {"prefix": prefix, **({"cursor": cursor} if cursor else {})}
            r = await self.http.get(f"{self.url}/objects/{bucket}", params=params, headers=await self._headers())
            r.raise_for_status()
            page = r.json()
            out += [Remote(o["key"], o["etag"]) for o in page["objects"]]
            cursor = page.get("next_cursor")
            if not cursor:
                return out

    async def put(self, bucket: str, key: str, body: bytes) -> None:
        r = await self.http.put(f"{self.url}/objects/{bucket}/{quote(key)}", content=body,
                                headers=await self._headers())
        r.raise_for_status()

    async def delete(self, bucket: str, key: str) -> None:
        r = await self.http.delete(f"{self.url}/objects/{bucket}/{quote(key)}", headers=await self._headers())
        r.raise_for_status()

    async def aclose(self) -> None:
        await self.http.aclose()


@dataclass
class SyncPlan:
    name: str
    sha: str
    source: str
    bucket: str
    prefix: str          # the key prefix in the bucket, ending in "/"
    files: list[str]     # repository paths under source


@dataclass
class Syncer:
    repo: Repo
    files: Files
    audit_log: Path
    _running: set[str] = field(default_factory=set)

    # ---- checks (nothing has changed yet) ------------------------------------------------------
    async def prepare(self, name: str, sha: str) -> SyncPlan:
        if not re.match(SYNC_RE, name) or not re.match(SHA_RE, sha):
            raise SyncError(422, "invalid sync or sha")
        if name in self._running:
            raise SyncError(409, f"sync {name} is already running")
        try:
            await self.repo.fetch()
        except RepoError as exc:
            raise SyncError(503, f"cannot fetch the repository: {exc}") from None
        if not await self.repo.exists(sha):
            raise SyncError(422, f"commit {sha[:12]} not found")
        if not await self.repo.on_main(sha):
            raise SyncError(422, f"commit {sha[:12]} is not on {self.repo.branch}")
        text = await self.repo.show(sha, f"infra/syncs/{name}.yaml")
        if text is None:
            raise SyncError(404, f"no infra/syncs/{name}.yaml at {sha[:12]}")
        try:
            manifest = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise SyncError(422, f"invalid sync manifest: {exc}") from None
        if not isinstance(manifest, dict) or manifest.get("sync") != name:
            raise SyncError(422, f"infra/syncs/{name}.yaml is not for {name}")
        source, destination = str(manifest.get("source", "")), str(manifest.get("destination", ""))
        if not _SOURCE.match(source) or any(part in (".", "..") for part in source.split("/")):
            raise SyncError(422, "source must be a folder in the repository")
        if not _DESTINATION.match(destination) or "/../" in destination or "/./" in destination:
            raise SyncError(422, "destination must be <bucket>/<prefix>/")
        try:
            files = await self.repo.files(sha, source)
        except RepoError as exc:
            raise SyncError(503, str(exc)) from None
        if not files:
            raise SyncError(422, f"{source} has no files at {sha[:12]}: refusing to empty {destination}")
        if len(files) > MAX_FILES:
            raise SyncError(422, f"{source} has {len(files)} files, over {MAX_FILES}")
        bucket, prefix = destination.split("/", 1)
        return SyncPlan(name, sha, source, bucket, prefix, files)

    # ---- the mirror ------------------------------------------------------------------------------
    async def run(self, plan: SyncPlan, requested_by: dict[str, Any]) -> dict[str, Any]:
        if plan.name in self._running:
            raise SyncError(409, f"sync {plan.name} is already running")
        self._running.add(plan.name)
        started = time.time()
        result: dict[str, Any] = {"sync": plan.name, "sha": plan.sha,
                                  "destination": f"{plan.bucket}/{plan.prefix}"}
        uploaded, unchanged, deleted = [], 0, []
        try:
            remote = {r.key: r.etag.strip('"') for r in await self.files.list(plan.bucket, plan.prefix)}
            wanted = set()
            for path in plan.files:
                key = plan.prefix + path[len(plan.source) + 1:]
                wanted.add(key)
                body = await self.repo.blob(plan.sha, path)
                if remote.get(key) == hashlib.md5(body).hexdigest():   # noqa: S324 (S3's ETag, not security)
                    unchanged += 1
                    continue
                await self.files.put(plan.bucket, key, body)
                uploaded.append(key[len(plan.prefix):])
            for key in sorted(set(remote) - wanted):
                await self.files.delete(plan.bucket, key)
                deleted.append(key[len(plan.prefix):])
            result.update(status="succeeded")
        except (RepoError, httpx.HTTPError) as exc:
            logger.exception("sync %s failed", plan.name)
            result.update(status="failed", error=str(exc)[:300])
        finally:
            self._running.discard(plan.name)
        result.update(files=len(plan.files), uploaded=uploaded, unchanged=unchanged, deleted=deleted,
                      seconds=round(time.time() - started, 1))
        self._audit(plan, requested_by, result)
        return result

    def _audit(self, plan: SyncPlan, requested_by: dict[str, Any], result: dict[str, Any]) -> None:
        entry = {"time": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "kind": "sync", "sync": plan.name,
                 "sha": plan.sha, "destination": result["destination"], "status": result.get("status"),
                 "uploaded": len(result.get("uploaded", [])), "deleted": len(result.get("deleted", [])),
                 "seconds": result.get("seconds"), "requested_by": requested_by}
        logger.info("sync %s", json.dumps(entry, sort_keys=True))
        try:
            self.audit_log.parent.mkdir(parents=True, exist_ok=True)
            with open(self.audit_log, "a") as f:
                f.write(json.dumps(entry, sort_keys=True) + "\n")
        except OSError:
            logger.exception("cannot write the audit log %s", self.audit_log)


async def run_sync(syncer: Syncer, name: str, sha: str, requested_by: dict[str, Any]) -> dict[str, Any]:
    """Prepare and run under one lock check (the request handler's entry point)."""
    plan = await syncer.prepare(name, sha)
    return await syncer.run(plan, requested_by)

