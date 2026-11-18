"""speech-service's API (speech-service.md, the API table): the two WebSocket sessions and the HTTP
routes, over one Service (the work threads and admissions)."""

from __future__ import annotations

from fastapi import FastAPI, WebSocket

from app.api.routes import router
from app.api.sessions import speech_session, transcription_session
from app.service import Service

AUDIENCE = "speech-service"


def create_api(service: Service, auth) -> FastAPI:
    app = FastAPI(title="speech-service", docs_url=None, redoc_url=None, openapi_url=None)
    app.include_router(router(service, auth))

    @app.websocket("/v1/realtime/speech")
    async def speech(ws: WebSocket):
        await speech_session(ws, service, auth)

    @app.websocket("/v1/realtime/transcription")
    async def transcription(ws: WebSocket):
        await transcription_session(ws, service, auth)

    app.state.service = service
    return app
