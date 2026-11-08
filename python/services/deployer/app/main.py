"""deployer: turns an approved GitHub Actions run into a deploy on the LAN. Spec: docs/specs/deployer.md.

    POST /v1/deployments   Authorization: Bearer <GitHub OIDC token>
                           {"service": "...", "sha": "<40 hex>", "image_digest": "sha256:..."?}
                           -> 200, NDJSON event stream (last line: the result)
    GET  /health

Reached from GitHub only through api-service's /deploy/* route (D3, D15); LAN-only otherwise.

Run (production, inside the container):
  uvicorn app.main:app --factory --host 0.0.0.0 --port 8443 --ssl-certfile ... --ssl-keyfile ...
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from service_auth import TokenClient

from app.config import Settings
from app.deploys import DIGEST_RE, SERVICE_RE, SHA_RE, Deployer, DeployError
from app.github_oidc import Forbidden, GitHubVerifier, Unauthorized, Unavailable
from app.hosts import HostsError, load_hosts
from app.openbao import OpenBaoSecrets
from app.repo import Repo
from app.ssh import SSHRunner

logger = logging.getLogger("deployer")
HEARTBEAT_SECONDS = 15  # keeps proxies (Cloudflare, api-service) from closing a quiet stream
MAX_BODY_BYTES = 4096


class DeploymentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    service: str = Field(pattern=SERVICE_RE)
    sha: str = Field(pattern=SHA_RE)
    image_digest: str | None = Field(default=None, pattern=DIGEST_RE)


def build_deployer(settings: Settings) -> tuple[Deployer, list]:
    """The real components; returns the deployer and what to close on shutdown."""
    tokens = TokenClient.from_env()
    secrets = OpenBaoSecrets(settings, tokens)
    deployer = Deployer(
        repo=Repo(settings.REPO_URL, settings.repo_dir, settings.BRANCH),
        secrets=secrets,
        runner=SSHRunner(settings.SSH_KEY_FILE, settings.DEPLOY_TIMEOUT_SECONDS),
        hosts_file=settings.HOSTS_FILE,
        audit_log=settings.audit_log,
    )
    return deployer, [secrets, tokens]


def _error(status: int, message: str) -> JSONResponse:
    headers = {"WWW-Authenticate": 'Bearer error="invalid_token"'} if status == 401 else None
    return JSONResponse({"error": message}, status_code=status, headers=headers)


def create_app(settings: Settings | None = None, *, deployer: Deployer | None = None,
               verifier: GitHubVerifier | None = None) -> FastAPI:
    settings = settings or Settings()
    logging.basicConfig(level=settings.LOG_LEVEL, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    closers: list = []
    if deployer is None:
        deployer, closers = build_deployer(settings)
    verifier = verifier or GitHubVerifier(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logger.info("deployer started: hosts=%s", settings.HOSTS_FILE)
        yield
        await deployer.wait()  # a stop (or redeploy of something else) never cuts a deploy in half
        await verifier.aclose()
        for c in closers:
            await c.aclose()

    # No /docs or /openapi.json: nothing to browse.
    app = FastAPI(title="deployer", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/health")
    async def health() -> JSONResponse:
        problems = []
        try:
            if not load_hosts(settings.HOSTS_FILE):
                problems.append("no hosts in the allowlist")
        except HostsError as exc:
            problems.append(str(exc))
        if not settings.SSH_KEY_FILE.is_file():
            problems.append("SSH key missing")
        body = {"status": "degraded" if problems else "healthy", "problems": problems}
        return JSONResponse(body, status_code=503 if problems else 200)

    @app.post("/v1/deployments")
    async def create_deployment(request: Request):
        # 1. Who is asking: a GitHub OIDC token, checked before the body is even read.
        auth = request.headers.get("authorization", "")
        if not auth.lower().startswith("bearer "):
            return _error(401, "a GitHub OIDC bearer token is required")
        try:
            claims = await verifier.verify(auth[7:].strip())
        except Unauthorized as exc:
            return _error(401, str(exc))
        except Forbidden as exc:
            logger.warning("refused GitHub token: %s", exc)
            return _error(403, str(exc))
        except Unavailable as exc:
            return _error(503, str(exc))

        # 2. What: service + sha (+ digest), nothing else (D4); the token is bound to the sha (D5).
        raw = await request.body()
        if len(raw) > MAX_BODY_BYTES:
            return _error(413, "request too large")
        try:
            body = DeploymentRequest.model_validate_json(raw)
        except ValidationError as exc:
            return _error(422, "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()))
        if claims.get("sha") != body.sha:
            return _error(422, "sha does not match the token's sha claim")

        # 3. Every check that can refuse, before anything runs.
        try:
            plan = await deployer.prepare(body.service, body.sha, body.image_digest)
            requested_by = {k: claims.get(k) for k in ("actor", "run_id", "run_attempt", "workflow", "sha")}
            queue = deployer.start(plan, requested_by)
        except DeployError as exc:
            return _error(exc.status, exc.message)

        return StreamingResponse(_ndjson(queue), media_type="application/x-ndjson")

    return app


def app() -> FastAPI:  # pragma: no cover - uvicorn factory: `uvicorn app.main:app --factory`
    """Factory for `uvicorn app.main:app --factory` (the Dockerfile's CMD)."""
    return create_app()


async def _ndjson(queue: asyncio.Queue):
    while True:
        try:
            event = await asyncio.wait_for(queue.get(), HEARTBEAT_SECONDS)
        except TimeoutError:
            yield '{"event": "heartbeat"}\n'
            continue
        if event is None:
            return
        yield json.dumps(event) + "\n"

