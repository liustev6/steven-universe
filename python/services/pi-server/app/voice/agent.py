"""agent-service, as pi-server (docs/specs/agent-service/control-plane.md). A voice session (voice-path.md
V4) is one agent session in a new conversation: `start` at the wake word claims it (and its pod), `end`
when the voice session goes idle releases it (`DELETE`). Its turns stream as protocol B over SSE.
agent-service's own backstop: a session idle 5 minutes is ended anyway (C6)."""

from __future__ import annotations

import json
import logging
from typing import AsyncIterator, Awaitable, Callable, Optional

import httpx

logger = logging.getLogger(__name__)


class AgentError(Exception):
    pass


class AgentClient:
    def __init__(self, url: str, token: Callable[[], Awaitable[str]], profile: str,
                 http: Optional[httpx.AsyncClient] = None):
        self.url, self.token, self.profile = url, token, profile
        self.http = http or httpx.AsyncClient(timeout=httpx.Timeout(15, read=180))
        self.conversation_id: Optional[str] = None
        self.session_id: Optional[str] = None

    async def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {await self.token()}"}

    async def start(self, conversation_id: Optional[str] = None) -> dict:
        """A session, in a new conversation (or `conversation_id`'s: a session that ended mid-way)."""
        body = {"profile": self.profile} | ({"conversation_id": conversation_id} if conversation_id else {})
        r = await self.http.post(f"{self.url}/v1/sessions", json=body, headers=await self._headers())
        if r.status_code not in (200, 201):
            raise AgentError(f"starting a session: {r.status_code} {r.text[:200]}")
        d = r.json()
        self.session_id, self.conversation_id = d["session_id"], d["conversation_id"]
        logger.info("agent session %s (conversation %s, %s)", self.session_id, self.conversation_id, d.get("state"))
        return d

    async def turn(self, text: str) -> AsyncIterator[dict]:
        """The turn's events as they come, up to `turn.completed`. With no session, one is started; a
        session that has ended (its limits, a restart) is replaced once, in the same conversation."""
        if self.session_id is None:
            await self.start()
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
                        event = json.loads(line[5:])
                        yield event
                        if event.get("type") == "turn.completed":
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
