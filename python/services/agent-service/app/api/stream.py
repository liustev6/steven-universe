"""Streams of protocol B events as Server-Sent Events (text/event-stream, the W3C EventSource format).

Each event:

    id: 12                         its `seq`: a client that reconnects sends it back as Last-Event-ID
    event: message.delta           its `type`
    data: {"v": 1, "seq": 12, …}   the whole protocol B event, envelope included

A comment line (`: keep-alive`) every 15 s without events keeps proxies from closing the stream.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi.responses import StreamingResponse

from app.history.eventlog import EventLog

HEARTBEAT = 15.0
HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}   # no proxy buffering


def frame(event: dict[str, Any]) -> bytes:
    return f"id: {event['seq']}\nevent: {event['type']}\ndata: {json.dumps(event, separators=(',', ':'))}\n\n".encode()


KEEP_ALIVE = b": keep-alive\n\n"


def ends_session(event: dict[str, Any]) -> bool:
    return event["type"] == "session.status" and event.get("status") == "ended"


async def session_events(log: EventLog, session_id: uuid.UUID, after: int) -> AsyncIterator[bytes]:
    """Everything after `after`, then live, until the session ends."""
    async for event in log.follow(session_id, after, heartbeat=HEARTBEAT):
        if event is None:
            yield KEEP_ALIVE
            continue
        yield frame(event)
        if ends_session(event):
            return


async def turn_events(log: EventLog, session_id: uuid.UUID, turn_id: uuid.UUID, after: int) -> AsyncIterator[bytes]:
    """One turn: its events and any session-level ones, until its turn.completed (or the session ends)."""
    turn = str(turn_id)
    async for event in log.follow(session_id, after, heartbeat=HEARTBEAT):
        if event is None:
            yield KEEP_ALIVE
            continue
        if event["turn_id"] not in (turn, None):
            continue
        yield frame(event)
        if (event["type"] == "turn.completed" and event["turn_id"] == turn) or ends_session(event):
            return


def sse(frames: AsyncIterator[bytes], status_code: int = 200, headers: dict[str, str] | None = None) -> StreamingResponse:
    return StreamingResponse(frames, status_code=status_code, media_type="text/event-stream",
                             headers={**HEADERS, **(headers or {})})
