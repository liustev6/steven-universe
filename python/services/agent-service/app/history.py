"""A conversation's history: its turns, across all its sessions (C8), and the carry-over (C9).

A new session in an existing conversation starts a fresh Codex thread, so it only knows what it's
told: the conversation's last `carry_turns` answered turns, as text after the profile's
instructions. Within a session, Codex keeps the whole thread itself.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Turn, TurnStatus

HEADER = "Earlier in this conversation (for context; continue from here):"


async def recent_turns(db: AsyncSession, conversation_id: uuid.UUID, n: int) -> list[Turn]:
    """The last `n` turns that got an answer (completed, or interrupted part-way), oldest first."""
    if n <= 0:
        return []
    rows = await db.scalars(
        select(Turn)
        .where(Turn.conversation_id == conversation_id,
               Turn.status.in_([TurnStatus.COMPLETED, TurnStatus.INTERRUPTED]),
               Turn.reply_text.is_not(None), Turn.reply_text != "")
        .order_by(Turn.started_at.desc()).limit(n))
    return list(reversed(rows.all()))


def context_text(turns: Sequence[Turn], max_chars: int) -> str:
    """The carried turns as plain text; each message cut to `max_chars` (a bounded, chosen cost)."""
    if not turns:
        return ""

    def cut(text: str) -> str:
        text = text.strip()
        return text if len(text) <= max_chars else text[:max_chars] + " […]"

    lines = [HEADER]
    for t in turns:
        lines.append(f"User: {cut(t.user_text)}")
        lines.append(f"Assistant: {cut(t.reply_text or '')}")
    return "\n".join(lines)
