"""The runtime's routes: how a sandbox gets its work and reports back (protocol A).

    GET  /v1/runtime/turns?wait=   the long poll: turns, interrupts, end (at most POLL_WAIT_MAX)
    POST /v1/runtime/events        a batch of events (at most 1 MB) → the sessions core
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import ValidationError

from app.gateway.pairing import read_limited
from app.sessions import Live, Sessions
from core.types import EventBatch

EVENTS_MAX_BYTES = 1 << 20


def build_router(sessions: Sessions, runtime) -> APIRouter:
    router = APIRouter()

    @router.get("/v1/runtime/turns")
    async def turns(wait: float = 25, live: Live = Depends(runtime)) -> dict:
        return {"messages": await sessions.next_messages(live.session_id, wait)}

    @router.post("/v1/runtime/events")
    async def events_in(request: Request, live: Live = Depends(runtime)) -> dict:
        body = await read_limited(request, EVENTS_MAX_BYTES)
        try:
            batch = EventBatch.model_validate_json(body)
        except ValidationError:
            raise HTTPException(422, "not an event batch")
        dropped = await sessions.ingest(live.session_id, batch.events)
        return {"ok": True, "dropped": dropped}

    return router
