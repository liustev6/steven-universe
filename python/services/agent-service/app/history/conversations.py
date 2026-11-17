"""Reading a caller's conversations and their turns (the sessions API's history routes, C8)."""

from __future__ import annotations

import uuid
from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ChatSession, Conversation, Event, SessionState, Turn


async def list_conversations(db: AsyncSession, owner: str | None, limit: int) -> list[dict[str, Any]]:
    """Most recently active first, with the live session if there is one. `owner=None`: everyone's."""
    q = select(Conversation).order_by(Conversation.last_active_at.desc()).limit(limit)
    if owner is not None:
        q = q.where(Conversation.owner == owner)
    conversations = (await db.scalars(q)).all()
    live = dict((await db.execute(
        select(ChatSession.conversation_id, ChatSession.id)
        .where(ChatSession.conversation_id.in_([c.id for c in conversations]),
               ChatSession.state != SessionState.ENDED))).all())
    return [{"conversation_id": str(c.id), "profile": c.profile, "owner": c.owner,
             "created_at": c.created_at.isoformat(), "last_active_at": c.last_active_at.isoformat(),
             "live_session_id": str(live[c.id]) if c.id in live else None} for c in conversations]


async def conversation_turns(db: AsyncSession, conversation_id: uuid.UUID, limit: int,
                             with_events: bool) -> list[dict[str, Any]]:
    """The last `limit` turns, oldest first; each with its protocol B events if asked."""
    rows = (await db.scalars(select(Turn).where(Turn.conversation_id == conversation_id)
                             .order_by(Turn.started_at.desc()).limit(limit))).all()
    turns = list(reversed(rows))
    events: dict[uuid.UUID, list] = defaultdict(list)
    if with_events and turns:
        for turn_id, payload in (await db.execute(
                select(Event.turn_id, Event.payload).where(Event.turn_id.in_([t.id for t in turns]))
                .order_by(Event.session_id, Event.seq))).all():
            events[turn_id].append(payload)
    out = []
    for t in turns:
        item = {"turn_id": str(t.id), "session_id": str(t.session_id), "user_text": t.user_text,
                "reply_text": t.reply_text, "status": t.status.value, "reason": t.reason,
                "first_text_ms": t.first_text_ms, "duration_ms": t.duration_ms, "usage": t.usage,
                "started_at": t.started_at.isoformat(),
                "completed_at": t.completed_at.isoformat() if t.completed_at else None}
        if with_events:
            item["events"] = events[t.id]
        out.append(item)
    return out
