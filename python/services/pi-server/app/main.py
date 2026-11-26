"""
pi-server - the local service hey-pi talks to.

hey-pi (the Flutter UI on the Pi) connects to the WebSocket at /ws and is told
the speech state (app/protocol.py). pi-server runs the voice assistant: the
wake word, the mic and the speaker, the voice turn (docs/specs/voice-path.md).

Run: uvicorn app.main:app --host 127.0.0.1 --port 8765
"""

import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI

from app.api import debug, health, ws
from app.core.config import Settings, settings as default_settings
from app.server import PiServer


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    settings = settings or default_settings
    logging.basicConfig(
        level=getattr(logging, settings.LOG_LEVEL),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    logger = logging.getLogger(__name__)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logger.info("Starting %s v%s (voice=%s)", settings.APP_NAME, settings.APP_VERSION, settings.VOICE)
        await app.state.pi.start()
        yield
        await app.state.pi.stop()
        logger.info("Stopped")

    app = FastAPI(title=settings.APP_NAME, version=settings.APP_VERSION, lifespan=lifespan)
    app.state.pi = PiServer(settings)
    app.include_router(health.router)
    app.include_router(ws.router)
    if settings.DEBUG_ENDPOINTS:
        app.include_router(debug.router)
    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=default_settings.HOST, port=default_settings.PORT)
