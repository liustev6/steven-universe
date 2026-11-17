"""sandbox-runner's client (docs/specs/agent-service/sandbox-runner.md "API").

    claim(template, session, capability, profile)  -> sandbox id     the runner writes the session file
    release(sandbox id)                                              destroy the pod
    claimed()                                       -> {sandbox ids}  pods still claimed; None if unknown

agent-service authenticates with its own auth-service token (audience sandbox-runner).
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Protocol

import httpx

logger = logging.getLogger(__name__)
SCOPES = ["sandboxes:claim", "sandboxes:release", "sandboxes:read"]


class NoSandbox(Exception):
    """No pod of that template is free (the runner's 503): try again shortly."""

    def __init__(self, retry_after: int = 5):
        super().__init__("no sandbox available")
        self.retry_after = retry_after


class RunnerError(Exception):
    """The runner refused for another reason, or couldn't be reached."""


class Runner(Protocol):
    async def claim(self, template: str, session_id: uuid.UUID, capability: str,
                    profile: dict[str, Any]) -> uuid.UUID: ...
    async def release(self, sandbox_id: uuid.UUID) -> None: ...
    async def claimed(self) -> set[uuid.UUID] | None: ...


class HttpRunner:
    def __init__(self, http: httpx.AsyncClient):
        self._http = http

    @classmethod
    def from_env(cls, base_url: str) -> "HttpRunner":
        from service_auth import TokenClient

        tokens = TokenClient.from_env()
        # The runner waits up to its CLAIM_WAIT_SECONDS (10) for a pod still warming up.
        return cls(httpx.AsyncClient(base_url=base_url, timeout=httpx.Timeout(10.0, read=30.0),
                                     auth=tokens.auth("sandbox-runner", scopes=SCOPES)))

    async def aclose(self) -> None:
        await self._http.aclose()

    async def claim(self, template: str, session_id: uuid.UUID, capability: str,
                    profile: dict[str, Any]) -> uuid.UUID:
        body = {"template": template, "session_id": str(session_id), "capability": capability, "profile": profile}
        try:
            r = await self._http.post("/v1/sandboxes/claim", json=body)
        except httpx.HTTPError as exc:
            raise RunnerError(f"runner unreachable: {exc!r}") from exc
        if r.status_code == 503:
            raise NoSandbox(_retry_after(r))
        if r.status_code != 200:
            raise RunnerError(f"claim refused: {r.status_code} {r.text[:200]}")
        return uuid.UUID(r.json()["sandbox_id"])

    async def release(self, sandbox_id: uuid.UUID) -> None:
        """Best effort: a pod that's already gone is fine, and one we fail to release is destroyed by
        the runner at its hard timeout anyway (R9)."""
        try:
            r = await self._http.post(f"/v1/sandboxes/{sandbox_id}/release")
            if r.status_code not in (200, 404):
                logger.warning("release %s: %s %s", sandbox_id, r.status_code, r.text[:200])
        except httpx.HTTPError as exc:
            logger.warning("release %s: runner unreachable (%r)", sandbox_id, exc)

    async def claimed(self) -> set[uuid.UUID] | None:
        try:
            r = await self._http.get("/v1/sandboxes")
            r.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("can't list the runner's pods (%r): no sandbox is judged lost this time", exc)
            return None
        return {uuid.UUID(p["sandbox_id"]) for p in r.json()["pods"] if p["state"] == "claimed"}


def _retry_after(r: httpx.Response) -> int:
    try:
        return max(1, int(r.headers.get("retry-after", "5")))
    except ValueError:
        return 5
