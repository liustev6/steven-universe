"""agent-service, as pi-server (docs/specs/agent-service/control-plane.md). A voice session (voice-path.md
V4) is one agent session in a new conversation: `start` at the wake word claims it (and its pod), `end`
when the voice session goes idle releases it (`DELETE`). Its turns stream as protocol B over SSE.
agent-service's own backstop: a session idle 5 minutes is ended anyway (C6)."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import AsyncIterator, Awaitable, Callable, Optional, Union
from zoneinfo import ZoneInfo

import httpx

from shared_schemas.agent_service import Body, TurnCompleted, parse_body

logger = logging.getLogger(__name__)


class AgentError(Exception):
    pass


def client_info(location: str, timezone: str) -> str:
    """What pi-server adds to the profile's instructions (agent-service's caller instructions,
    control-plane.md C15): facts about the panel that don't change, as a block the profile's rules
    refer to. The time isn't here: it changes every session, and the instructions are the start of
    every model call's prompt, so it would end the part the prompt cache can share (`now_line`)."""
    lines = ["Additional Client Info:"]
    if timezone:
        lines.append(f"timezone: {timezone}")
    if location:
        lines.append(f"City, Country: {location}")
    return "\n".join(lines)


def now_line(timezone: str, now: Optional[datetime] = None) -> str:
    """The time, at the start of each turn's text: where it changes, at the end of the prompt."""
    tz = ZoneInfo(timezone) if timezone else UTC
    now = (now or datetime.now(UTC)).astimezone(tz)
    return f"[now: {now:%Y-%m-%d %H:%M} ({now:%A}, UTC{now:%z})]"


class AgentClient:
    def __init__(self, url: str, token: Callable[[], Awaitable[str]], profile: str,
                 http: Optional[httpx.AsyncClient] = None,
                 instructions: Union[str, Callable[[], str], None] = None,
                 turn_prefix: Optional[Callable[[], str]] = None):
        self.url, self.token, self.profile, self.instructions = url, token, profile, instructions
        self.turn_prefix = turn_prefix          # a line put before each turn's text (the time)
        self.http = http or httpx.AsyncClient(timeout=httpx.Timeout(15, read=180))
        self.conversation_id: Optional[str] = None
        self.session_id: Optional[str] = None

    async def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {await self.token()}"}

    async def start(self, conversation_id: Optional[str] = None) -> dict:
        """A session, in a new conversation (or `conversation_id`'s: a session that ended mid-way)."""
        instructions = self.instructions() if callable(self.instructions) else self.instructions
        body = ({"profile": self.profile} | ({"conversation_id": conversation_id} if conversation_id else {})
                | ({"instructions": instructions} if instructions else {}))
        r = await self.http.post(f"{self.url}/v1/sessions", json=body, headers=await self._headers())
        if r.status_code not in (200, 201):
            raise AgentError(f"starting a session: {r.status_code} {r.text[:200]}")
        d = r.json()
        self.session_id, self.conversation_id = d["session_id"], d["conversation_id"]
        logger.info("agent session %s (conversation %s, %s)", self.session_id, self.conversation_id, d.get("state"))
        return d

    async def turn(self, text: str) -> AsyncIterator[Body]:
        """The turn's events (protocol B's bodies; types it doesn't know skipped) as they come, up to
        `turn.completed`. With no session, one is started; a
        session that has ended (its limits, a restart) is replaced once, in the same conversation."""
        if self.session_id is None:
            await self.start()
        if self.turn_prefix is not None:
            text = f"{self.turn_prefix()}\n{text}"
        for attempt in range(2):
            async with self.http.stream("POST", f"{self.url}/v1/sessions/{self.session_id}/turns",
                                        json={"text": text}, headers=await self._headers()) as r:
                if r.status_code in (404, 409) and attempt == 0:
                    logger.info("session %s refused the turn (%s): a new one", self.session_id, r.status_code)
                    await self.start(self.conversation_id)
                    continue
                if r.status_code != 200:
                    raise AgentError(f"turn: {r.status_code} {(await r.aread())[:200]!r}")
                async for line in r.aiter_lines():
                    if line.startswith("data:"):
                        event = parse_body(json.loads(line[5:]))       # the tolerant reader: new types, new fields
                        if event is None:
                            continue
                        yield event
                        if isinstance(event, TurnCompleted):
                            return
                raise AgentError("the turn's stream ended before turn.completed")

    async def cancel(self) -> None:
        """Interrupt the running turn (it still ends with turn.completed, interrupted)."""
        if self.session_id:
            try:
                await self.http.post(f"{self.url}/v1/sessions/{self.session_id}/cancel", headers=await self._headers())
            except httpx.HTTPError as e:
                logger.warning("cancelling the turn: %s", e)

    async def end(self) -> None:
        """Release the session and its pod. The next `turn` or `start` begins a new conversation."""
        session, self.session_id, self.conversation_id = self.session_id, None, None
        if session:
            try:
                r = await self.http.delete(f"{self.url}/v1/sessions/{session}", headers=await self._headers())
                logger.info("agent session %s released (%s)", session, r.status_code)
            except httpx.HTTPError as e:                     # agent-service ends it after 5 idle minutes anyway
                logger.warning("releasing session %s: %s", session, e)

    async def close(self) -> None:
        await self.http.aclose()
