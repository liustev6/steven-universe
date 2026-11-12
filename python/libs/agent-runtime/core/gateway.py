"""The runtime's only connection: outbound to its gateway (agent-runtime.md; sandbox-runner R5).

    GET  /v1/runtime/turns?wait=<s>   long poll for turns, interrupts, end
    POST /v1/runtime/events           a batch of events

Authenticated with the session capability, the only credential a sandbox holds.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from core.types import EventBatch, InboundBatch, build_event

logger = logging.getLogger(__name__)
POLL_WAIT_SECONDS = 25


class GatewayClient:
    def __init__(self, base_url: str, capability: str, *, http: httpx.AsyncClient | None = None):
        self.base_url = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {capability}"}
        self._http = http or httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=POLL_WAIT_SECONDS + 10))
        self._owns_http = http is None

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def next_messages(self, wait: int = POLL_WAIT_SECONDS) -> InboundBatch:
        """Turns/interrupts/end, waiting up to `wait` seconds; an empty batch on timeout."""
        r = await self._http.get(f"{self.base_url}/v1/runtime/turns", params={"wait": wait}, headers=self._headers)
        r.raise_for_status()
        return InboundBatch.model_validate(r.json())

    async def send_events(self, events: list[dict[str, Any]]) -> None:
        r = await self._http.post(f"{self.base_url}/v1/runtime/events",
                                  json=EventBatch(events=events).model_dump(), headers=self._headers)
        r.raise_for_status()


class EventSink:
    """Numbers events (seq) and ships them in natural batches: whatever queued while the last POST
    was in flight goes in the next one, so streaming text isn't held back by a timer."""

    def __init__(self, gateway: GatewayClient, session_id: str):
        self._gateway = gateway
        self._session_id = session_id
        self._seq = 0
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._task: asyncio.Task | None = None

    def emit(self, payload: dict[str, Any], turn_id: str | None = None) -> None:
        """Add the envelope, validate against protocol A (a malformed event raises: a bug), queue it."""
        event = build_event(payload, session_id=self._session_id, turn_id=turn_id, seq=self._seq, ts=time.time())
        self._seq += 1
        self._queue.put_nowait(event.model_dump(mode="json"))

    def start(self) -> None:
        self._task = asyncio.create_task(self._ship())

    async def flush(self) -> None:
        await self._queue.join()

    async def close(self) -> None:
        await self.flush()
        if self._task:
            self._task.cancel()

    async def _ship(self) -> None:
        while True:
            batch = [await self._queue.get()]
            while not self._queue.empty():
                batch.append(self._queue.get_nowait())
            for attempt in range(5):
                try:
                    await self._gateway.send_events(batch)
                    break
                except httpx.HTTPError as exc:
                    logger.warning("sending %d events failed (%r), retrying", len(batch), exc)
                    await asyncio.sleep(0.2 * 2 ** attempt)
            for _ in batch:
                self._queue.task_done()
