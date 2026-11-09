"""api-service: the only service reachable from the internet. Spec: docs/specs/api-service.md.

Minimal for now: `GET /health` and the deploy routes (deployer.md D3, D15). The other routes
(files, chat, user login) arrive one at a time, ported from web-gateway and fixed on the way
(spec Migration).

Run (production, inside the container):
  uvicorn app.main:app --factory --host 0.0.0.0 --port 8443 --ssl-certfile ... --ssl-keyfile ...
"""

from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager

import httpx
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import deploy
from app.config import Settings
from app.edge import Mode, access, client_ip

logger = logging.getLogger("api_service")

SECURITY_HEADERS = {
    "Strict-Transport-Security": "max-age=31536000",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


def create_app(settings: Settings | None = None, *, http: httpx.AsyncClient | None = None) -> FastAPI:
    settings = settings or Settings()
    logging.basicConfig(level=settings.LOG_LEVEL, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    owns_http = http is None
    http = http or httpx.AsyncClient(timeout=httpx.Timeout(
        connect=5.0, read=settings.DEPLOY_READ_TIMEOUT_SECONDS, write=10.0, pool=5.0))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logger.info("api-service started: deployer=%s", settings.DEPLOYER_URL)
        yield
        if owns_http:
            await http.aclose()

    # No /docs or /openapi.json on the public edge.
    app = FastAPI(title="api-service", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def pipeline(request: Request, call_next):
        # Request ID (spec "Request pipeline" 1); one log line per request, never credentials.
        request.state.request_id = uuid.uuid4().hex
        started = time.monotonic()
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        response.headers.setdefault("Cache-Control", "no-store")
        response.headers["X-Request-ID"] = request.state.request_id
        logger.info("%s %s %d %.0fms ip=%s id=%s", request.method, request.url.path, response.status_code,
                    (time.monotonic() - started) * 1000, client_ip(request, settings), request.state.request_id)
        return response

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "error")
        return JSONResponse({"error": code, "message": code.replace("_", " "),
                             "request_id": getattr(request.state, "request_id", None)},
                            status_code=exc.status_code, headers=getattr(exc, "headers", None))

    @app.get("/health", dependencies=[Depends(access(Mode.PUBLIC))])
    async def health() -> dict:
        return {"status": "ok"}   # liveness only: no internal details (spec G6)

    # Route groups, one package each (app/<group>/): today only deploys.
    app.include_router(deploy.build_router(settings, http))
    return app


def app() -> FastAPI:  # pragma: no cover - uvicorn factory: `uvicorn app.main:app --factory`
    """Factory for `uvicorn app.main:app --factory` (the Dockerfile's CMD)."""
    return create_app()
