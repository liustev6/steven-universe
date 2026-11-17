"""History (C8): a caller's conversations and their turns, across all their sessions.

    GET /v1/conversations                       chat:read   mine, most recently active first
    GET /v1/conversations/{id}/turns            chat:read   its turns, oldest first
          ?limit=100  ?events=true                          (each turn's protocol B events too)
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from service_auth import Principal

from app.api.sessions import owner_scope
from app.history.conversations import conversation_turns, list_conversations
from app.sessions import Sessions


def build_router(sessions: Sessions, auth) -> APIRouter:
    router = APIRouter(prefix="/v1/conversations")
    read = auth.require("chat:read")

    @router.get("")
    async def mine(limit: int = Query(50, ge=1, le=500), p: Principal = Depends(read)) -> dict:
        async with sessions.db() as db:
            return {"conversations": await list_conversations(db, owner_scope(p), limit)}

    @router.get("/{conversation_id}/turns")
    async def turns(conversation_id: uuid.UUID, limit: int = Query(100, ge=1, le=1000),
                    events: bool = False, p: Principal = Depends(read)) -> dict:
        conversation = await sessions.conversation(conversation_id, owner_scope(p))     # 404 if not yours
        async with sessions.db() as db:
            return {"conversation_id": str(conversation.id), "profile": conversation.profile,
                    "turns": await conversation_turns(db, conversation_id, limit, events)}

    return router
