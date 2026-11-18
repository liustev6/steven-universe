"""Sessions: start, talk, follow, end (control-plane.md "The sessions API").

    POST   /v1/sessions                 chat:write   201 a new session, 200 the conversation's live one
    POST   /v1/sessions/{id}/turns      chat:write   one turn, streamed until its turn.completed
    GET    /v1/sessions/{id}/events     chat:read    everything after a seq, then live (reconnect)
    GET    /v1/sessions/{id}            chat:read    its state
    POST   /v1/sessions/{id}/cancel     chat:write   interrupt the running turn
    DELETE /v1/sessions/{id}            chat:write   end it; the pod is released

A session is its owner's (the token's subject); anyone else gets 404 (C2). `chat:admin` reaches
every session.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from service_auth import Principal

from app.api import stream
from app.db.models import ChatSession, EndReason
from app.profiles import CALLER_INSTRUCTIONS_MAX
from app.sessions import Sessions


class StartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile: str = Field(min_length=1, max_length=32)
    conversation_id: uuid.UUID | None = None      # continue this conversation; none = a new one
    # Added to the profile's instructions when the profile allows it (C15): where the caller is, whom
    # it serves. A live session keeps the instructions it started with.
    instructions: str | None = Field(None, max_length=CALLER_INSTRUCTIONS_MAX)


class TurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=32_000)


def owner_scope(p: Principal) -> str | None:
    """Whose sessions this caller may reach: its own, or (chat:admin) everyone's."""
    return None if "chat:admin" in p.scopes else p.subject


def describe(row: ChatSession) -> dict:
    return {"session_id": str(row.id), "conversation_id": str(row.conversation_id), "state": row.state.value,
            "end_reason": row.end_reason.value if row.end_reason else None, "turns_used": row.turns_used,
            "started_at": row.started_at.isoformat() if row.started_at else None,
            "ready_at": row.ready_at.isoformat() if row.ready_at else None,
            "ended_at": row.ended_at.isoformat() if row.ended_at else None, "instructions": row.instructions}


def build_router(sessions: Sessions, auth) -> APIRouter:
    router = APIRouter(prefix="/v1/sessions")
    write, read = auth.require("chat:write"), auth.require("chat:read")

    @router.post("")
    async def start(body: StartRequest, p: Principal = Depends(write)) -> JSONResponse:
        row, created = await sessions.start(p.subject, body.profile, body.conversation_id, body.instructions)
        return JSONResponse(describe(row), status_code=201 if created else 200)

    @router.post("/{session_id}/turns")
    async def turn(session_id: uuid.UUID, body: TurnRequest, p: Principal = Depends(write)):
        t, after = await sessions.turn(session_id, owner_scope(p), body.text)
        return stream.sse(stream.turn_events(sessions.log, session_id, t.id, after),
                          headers={"X-Turn-Id": str(t.id)})

    @router.get("/{session_id}/events")
    async def events(session_id: uuid.UUID, after: int = Query(-1, ge=-1),
                     last_event_id: str | None = Header(None), p: Principal = Depends(read)):
        await sessions.get(session_id, owner_scope(p))                       # 404 if not yours
        if last_event_id is not None and last_event_id.isdigit():           # an EventSource reconnecting
            after = int(last_event_id)
        return stream.sse(stream.session_events(sessions.log, session_id, after))

    @router.get("/{session_id}")
    async def get(session_id: uuid.UUID, p: Principal = Depends(read)) -> dict:
        return describe(await sessions.get(session_id, owner_scope(p)))

    @router.post("/{session_id}/cancel")
    async def cancel(session_id: uuid.UUID, p: Principal = Depends(write)) -> dict:
        return {"interrupted": await sessions.cancel(session_id, owner_scope(p))}

    @router.delete("/{session_id}")
    async def end(session_id: uuid.UUID, p: Principal = Depends(write)) -> dict:
        ended = await sessions.end(session_id, EndReason.DELETED, owner_scope(p))
        return {"ended": ended}

    return router
