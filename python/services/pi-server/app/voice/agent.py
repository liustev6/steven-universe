"""agent-service, as pi-server (docs/specs/agent-service/control-plane.md): one conversation kept while
you talk to it (voice-path.md V4), its turns streamed as protocol B events over SSE."""

from __future__ import annotations

import json
import logging
import time
from typing import AsyncIterator, Awaitable, Callable, Optional

import httpx

logger = logging.getLogger(__name__)


class AgentError(Exception):
    pass


class AgentClient:
    def __init__(self, url: str, token: Callable[[], Awaitable[str]], profile: str,
                 conversation_idle_seconds: float, http: Optional[httpx.AsyncClient] = None,
                 clock: Callable[[], float] = time.monotonic):
        self.url, self.token, self.profile = url, token, profile
        self.idle = conversation_idle_seconds
        self.http = http or httpx.AsyncClient(timeout=httpx.Timeout(15, read=180))
        self.clock = clock
        self.conversation_id: Optional[str] = None
        self.session_id: Optional[str] = None
        self.last_turn: Optional[float] = None

    async def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {await self.token()}"}

    def _stale(self) -> bool:
        return self.last_turn is not None and self.clock() - self.last_turn > self.idle

    async def prepare(self) -> dict:
        """The conversation's live session, started now if it has none (so it's ready by the end of
        the sentence). After `idle` seconds without a turn, a new conversation."""
        if self._stale():
            logger.info("conversation %s: %.0f s without a turn, starting a new one",
                        self.conversation_id, self.clock() - self.last_turn)
            self.conversation_id = self.session_id = None
            self.last_turn = None
        body = {"profile": self.profile} | ({"conversation_id": self.conversation_id} if self.conversation_id else {})
        r = await self.http.post(f"{self.url}/v1/sessions", json=body, headers=await self._headers())
        if r.status_code not in (200, 201):
            raise AgentError(f"starting a session: {r.status_code} {r.text[:200]}")
        d = r.json()
        self.session_id, self.conversation_id = d["session_id"], d["conversation_id"]
        return d

    async def turn(self, text: str) -> AsyncIterator[dict]:
        """The turn's events as they come, up to `turn.completed`. A session that has ended (its
        limits, a restart) is replaced once, in the same conversation."""
        if self.session_id is None or self._stale():
            await self.prepare()
        self.last_turn = self.clock()
        for attempt in range(2):
            async with self.http.stream("POST", f"{self.url}/v1/sessions/{self.session_id}/turns",
                                        json={"text": text}, headers=await self._headers()) as r:
                if r.status_code in (404, 409) and attempt == 0:
                    logger.info("session %s refused the turn (%s): a new one", self.session_id, r.status_code)
                    await self.prepare()
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
        if self.session_id:
            try:
                await self.http.post(f"{self.url}/v1/sessions/{self.session_id}/cancel", headers=await self._headers())
            except httpx.HTTPError as e:
                logger.warning("cancelling the turn: %s", e)

    async def close(self) -> None:
        await self.http.aclose()
