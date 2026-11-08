"""file-management: the only service that talks to object storage. Spec: docs/specs/file-management.md.

Run (production, inside the container):
  uvicorn app.main:app --factory --host 0.0.0.0 --port 8443 --ssl-certfile ... --ssl-keyfile ...

Local development without RustFS or auth-service:
  ENV=local FILES_STORAGE=memory \
  SERVICE_AUTH_DEV_PRINCIPAL='{"service":"dev","scopes":["*"],"authorization_details":[...]}' \
  uvicorn app.main:app --factory --port 8000
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from service_auth.fastapi_auth import ServiceAuth

from app.api.objects import build_router
from app.authz import BadKey, Forbidden
from app.config import Settings
from app.storage import NotFound, NotModified, RangeNotSatisfiable, Storage, StorageError, TooLarge

AUDIENCE = "file-management"
logger = logging.getLogger("file_management")


def _make_storage(settings: Settings) -> Storage:
    if os.environ.get("FILES_STORAGE") == "memory":
        if os.environ.get("ENV") != "local":
            raise RuntimeError("FILES_STORAGE=memory is only allowed with ENV=local")
        from app.storage.memory import MemoryStorage

        return MemoryStorage(settings.BUCKETS)
    from app.storage.s3 import S3Storage

    access_key, secret_key = settings.storage_credentials()
    return S3Storage(
        settings.STORAGE_ENDPOINT,
        access_key,
        secret_key,
        region=settings.STORAGE_REGION,
        part_size=settings.PART_SIZE_BYTES,
        timeout=settings.STORAGE_TIMEOUT_SECONDS,
    )


def create_app(
    settings: Settings | None = None,
    *,
    storage: Storage | None = None,
    auth: ServiceAuth | None = None,
) -> FastAPI:
    settings = settings or Settings()
    logging.basicConfig(level=settings.LOG_LEVEL, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    storage = storage or _make_storage(settings)
    auth = auth or ServiceAuth.from_env(AUDIENCE)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await storage.start()
        if not await storage.check(settings.BUCKETS):
            logger.warning("storage not ready: buckets %s missing or unreachable", settings.BUCKETS)
        logger.info("file-management started: buckets=%s", settings.BUCKETS)
        yield
        await storage.close()

    # No /docs or /openapi.json: internal service, nothing to browse (spec P6).
    app = FastAPI(title="file-management", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.storage = storage
    app.include_router(build_router(auth))

    @app.get("/health")
    async def health() -> Response:
        ok = await storage.check(settings.BUCKETS)
        body = {"status": "healthy" if ok else "degraded", "storage": "ok" if ok else "unavailable"}
        return JSONResponse(body, status_code=200 if ok else 503)

    # Errors: generic bodies; the reason goes to logs (spec P6).
    def error(status: int, detail: str, headers: dict[str, str] | None = None):
        async def handler(request: Request, exc: Exception) -> Response:
            if status == 304:
                return Response(status_code=304)
            return JSONResponse({"detail": detail}, status_code=status, headers=headers)

        return handler

    app.add_exception_handler(BadKey, error(400, "invalid bucket or key"))
    app.add_exception_handler(
        Forbidden, error(403, "forbidden", {"WWW-Authenticate": 'Bearer error="insufficient_scope"'})
    )
    app.add_exception_handler(NotFound, error(404, "not found"))
    app.add_exception_handler(NotModified, error(304, ""))
    app.add_exception_handler(TooLarge, error(413, "too large"))
    app.add_exception_handler(RangeNotSatisfiable, error(416, "range not satisfiable"))
    app.add_exception_handler(StorageError, error(502, "storage error"))
    return app


def app() -> FastAPI:
    """Factory for `uvicorn app.main:app --factory`."""
    return create_app()
