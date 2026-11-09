"""The deploy itself: checks, secrets, deploy-target over SSH, rollback, audit (deployer spec).

    prepare()  everything that can refuse a request before anything runs (-> 4xx):
               the commit, the manifest at that commit, `pipeline`, the host allowlist, the lock
    start()    runs the deploy as a background task and returns its Deployment. The task is not
               tied to the HTTP request: if the caller disconnects, the deploy still finishes (and
               rolls back if needed) instead of being cut off halfway.

A Deployment keeps its numbered events, so a caller whose connection dropped (e.g. GitHub, while
api-service itself is being replaced) can reconnect and continue after the last `seq` it saw:
`follow(after=<seq>)`, served as GET /v1/deployments/<id>/events?after=<seq>.

Events (one JSON object per line on the wire; all but heartbeats carry "seq": 0, 1, 2, ...):
    {"event": "accepted", "id": "...", ...}    {"event": "step", "n": 2, "of": 8, "name": "..."}
    {"event": "log", "line": "..."}            {"event": "rollback", "to": "<sha>", "reason": "..."}
    {"event": "result", "status": "succeeded" | "failed" | "rolled_back", ...}
    {"event": "heartbeat"}                     while nothing happens (keeps proxies from closing)
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable, Protocol

import jsonschema
import yaml

from app.hosts import Host, HostsError, load_hosts
from app.openbao import SecretsError
from app.repo import Repo, RepoError

logger = logging.getLogger(__name__)

SERVICE_RE = r"^[a-z][a-z0-9-]{1,62}$"
SHA_RE = r"^[0-9a-f]{40}$"
DIGEST_RE = r"^sha256:[0-9a-f]{64}$"
SCHEMA_PATH = "infra/services/deploy.schema.json"

_STEP = re.compile(r"^Step (\d+)/(\d+): (.+)$")
_PREVIOUS = re.compile(r"^\s*previous: ([0-9a-f]{40})\s*$")

Emit = Callable[[dict[str, Any]], Awaitable[None]]
KEEP_DEPLOYMENTS = 100          # finished deployments kept in memory for reconnects
MAX_EVENTS = 5000               # per deployment (a deploy prints ~100 lines)


class Deployment:
    """One deploy: its numbered events, readable from any point while and after it runs."""

    def __init__(self, service: str, sha: str):
        self.id = uuid.uuid4().hex
        self.service = service
        self.sha = sha
        self.events: list[dict[str, Any]] = []
        self.done = False
        self._changed = asyncio.Condition()

    async def emit(self, event: dict[str, Any]) -> None:
        async with self._changed:
            if len(self.events) < MAX_EVENTS or event.get("event") == "result":
                self.events.append({"seq": len(self.events), **event})
            self._changed.notify_all()

    async def finish(self) -> None:
        async with self._changed:
            self.done = True
            self._changed.notify_all()

    async def follow(self, after: int = -1, heartbeat: float = 15.0) -> AsyncIterator[dict[str, Any]]:
        """Events with seq > after, then new ones as they come; ends when the deploy is over."""
        position = max(after + 1, 0)
        while True:
            async with self._changed:
                if position >= len(self.events) and not self.done:
                    try:
                        await asyncio.wait_for(self._changed.wait(), heartbeat)
                    except TimeoutError:
                        pass
                fresh, done = self.events[position:], self.done
            position += len(fresh)
            for event in fresh:
                yield event
            if done and position >= len(self.events):
                return
            if not fresh:
                yield {"event": "heartbeat"}


class DeployError(Exception):
    """Refused before anything ran; `status` is the HTTP status."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class SecretsSource(Protocol):
    async def payload(self, declared: list[dict[str, Any]]) -> bytes: ...


class Runner(Protocol):
    async def run(self, host: Host, service: str, sha: str, digest: str | None, payload: bytes,
                  on_line: Callable[[str], Awaitable[None]]) -> int: ...


@dataclass
class Plan:
    service: str
    sha: str
    digest: str | None
    manifest: dict[str, Any]
    host: Host


@dataclass
class _Attempt:
    exit_code: int = 0
    last_step: int = 0          # highest "Step N/M" seen; >= 2 means the host may have changed
    previous: str | None = None
    error: str | None = None


@dataclass
class Deployer:
    repo: Repo
    secrets: SecretsSource
    runner: Runner
    hosts_file: Path
    audit_log: Path
    _running: set[str] = field(default_factory=set)
    _tasks: set[asyncio.Task] = field(default_factory=set)
    deployments: OrderedDict = field(default_factory=OrderedDict)   # id -> Deployment

    # ---- checks (nothing has run yet) -----------------------------------------------------------
    async def prepare(self, service: str, sha: str, digest: str | None = None, *,
                      require_pipeline: bool = True, require_main: bool = True) -> Plan:
        if not re.match(SERVICE_RE, service) or not re.match(SHA_RE, sha):
            raise DeployError(422, "invalid service or sha")
        if digest is not None and not re.match(DIGEST_RE, digest):
            raise DeployError(422, "invalid image digest")
        if service in self._running:
            raise DeployError(409, f"a deploy of {service} is already running")
        try:
            await self.repo.fetch()
        except RepoError as exc:
            raise DeployError(503, f"cannot fetch the repository: {exc}") from None
        if not await self.repo.exists(sha):
            raise DeployError(422, f"commit {sha[:12]} not found")
        if require_main and not await self.repo.on_main(sha):
            raise DeployError(422, f"commit {sha[:12]} is not on {self.repo.branch}")
        if not require_main and not await self.repo.on_any_branch(sha):
            raise DeployError(422, f"commit {sha[:12]} is not on any pushed branch")
        manifest = await self.manifest(service, sha)
        if require_pipeline and manifest.get("pipeline") is not True:
            raise DeployError(403, f"{service} is deployed by hand only (pipeline: false in its manifest)")
        return Plan(service, sha, digest, manifest, self.host(manifest))

    async def manifest(self, service: str, sha: str) -> dict[str, Any]:
        text = await self.repo.show(sha, f"infra/services/{service}/deploy.yaml")
        if text is None:
            raise DeployError(404, f"no infra/services/{service}/deploy.yaml at {sha[:12]}")
        try:
            manifest = yaml.safe_load(text)
            schema_text = await self.repo.show(sha, SCHEMA_PATH)
            if schema_text is not None:  # the schema as of that commit
                jsonschema.validate(manifest, json.loads(schema_text))
        except (yaml.YAMLError, jsonschema.ValidationError, ValueError) as exc:
            raise DeployError(422, f"invalid manifest at {sha[:12]}: {getattr(exc, 'message', exc)}") from None
        if not isinstance(manifest, dict) or manifest.get("service") != service:
            raise DeployError(422, f"manifest at {sha[:12]} is not for {service}")
        return manifest

    def host(self, manifest: dict[str, Any]) -> Host:
        try:
            hosts = load_hosts(self.hosts_file)
        except HostsError as exc:
            raise DeployError(503, str(exc)) from None
        host = hosts.get(str(manifest.get("host")))
        if host is None:
            raise DeployError(403, f"host {manifest.get('host')!r} is not in the deployer's allowlist")
        return host

    # ---- the deploy (background task) -----------------------------------------------------------
    def start(self, plan: Plan, requested_by: dict[str, Any]) -> Deployment:
        if plan.service in self._running:
            raise DeployError(409, f"a deploy of {plan.service} is already running")
        self._running.add(plan.service)
        deployment = Deployment(plan.service, plan.sha)
        self.deployments[deployment.id] = deployment
        while len(self.deployments) > KEEP_DEPLOYMENTS:
            oldest = next(iter(self.deployments.values()))
            if not oldest.done:
                break
            self.deployments.popitem(last=False)

        async def run() -> None:
            try:
                await self._deploy(plan, requested_by, deployment)
            finally:
                await deployment.finish()

        task = asyncio.create_task(run())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return deployment

    def get(self, deployment_id: str) -> Deployment | None:
        return self.deployments.get(deployment_id)

    async def wait(self) -> None:
        """Let running deploys finish (shutdown)."""
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    async def _deploy(self, plan: Plan, requested_by: dict[str, Any], deployment: Deployment) -> None:
        emit = deployment.emit
        started = time.time()
        result: dict[str, Any] = {"event": "result", "id": deployment.id, "service": plan.service, "sha": plan.sha}
        try:
            await emit({"event": "accepted", "id": deployment.id, "service": plan.service, "sha": plan.sha,
                        "host": plan.host.name, "digest": plan.digest})
            first = await self._attempt(plan, emit)
            result["previous"] = first.previous
            if first.exit_code == 0:
                result["status"] = "succeeded"
            elif first.last_step >= 2 and first.previous and first.previous != plan.sha:
                result["status"] = await self._rollback(plan, first, emit)
            else:
                result["status"] = "failed"
                result["error"] = first.error or "deploy failed before changing the host; nothing to roll back"
        except Exception as exc:  # never leave the stream without a result
            logger.exception("deploy of %s failed", plan.service)
            result.update(status="failed", error=f"deployer error: {exc}")
        finally:
            self._running.discard(plan.service)
            result["seconds"] = round(time.time() - started)
            self._audit(plan, requested_by, result)
            await emit(result)

    async def _rollback(self, plan: Plan, first: _Attempt, emit: Emit) -> str:
        to = first.previous
        assert to
        await emit({"event": "rollback", "to": to, "reason": f"deploy of {plan.sha[:12]} failed"})
        try:
            # What was running before: its own manifest and secrets. Not limited to `main` or
            # `pipeline: true` (it may have been deployed by hand), but the host must be the same.
            manifest = await self.manifest(plan.service, to)
            if manifest.get("host") != plan.manifest.get("host"):
                raise DeployError(422, "the previous commit deploys to another host")
            back = await self._attempt(Plan(plan.service, to, None, manifest, self.host(manifest)), emit)
        except DeployError as exc:
            await emit({"event": "log", "line": f"rollback not possible: {exc.message}"})
            return "failed"
        return "rolled_back" if back.exit_code == 0 else "failed"

    async def _attempt(self, plan: Plan, emit: Emit) -> _Attempt:
        attempt = _Attempt()
        try:
            payload = await self.secrets.payload(plan.manifest.get("secrets") or [])
        except SecretsError as exc:
            attempt.exit_code, attempt.error = 1, f"secrets: {exc}"
            await emit({"event": "log", "line": attempt.error})
            return attempt

        async def on_line(line: str) -> None:
            if m := _STEP.match(line):
                attempt.last_step = max(attempt.last_step, int(m[1]))
                await emit({"event": "step", "n": int(m[1]), "of": int(m[2]), "name": m[3]})
            elif m := _PREVIOUS.match(line):
                attempt.previous = m[1]
            await emit({"event": "log", "line": line})

        attempt.exit_code = await self.runner.run(plan.host, plan.service, plan.sha, plan.digest, payload, on_line)
        return attempt

    def _audit(self, plan: Plan, requested_by: dict[str, Any], result: dict[str, Any]) -> None:
        entry = {"time": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "service": plan.service, "sha": plan.sha,
                 "digest": plan.digest, "host": plan.host.name, "status": result.get("status"),
                 "previous": result.get("previous"), "seconds": result.get("seconds"),
                 "requested_by": requested_by}
        logger.info("deploy %s", json.dumps(entry, sort_keys=True))
        try:
            self.audit_log.parent.mkdir(parents=True, exist_ok=True)
            with open(self.audit_log, "a") as f:
                f.write(json.dumps(entry, sort_keys=True) + "\n")
        except OSError:
            logger.exception("cannot write the audit log %s", self.audit_log)
