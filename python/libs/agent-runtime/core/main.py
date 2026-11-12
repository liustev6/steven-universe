"""agent-runtime's main loop (agent-runtime.md, "What it is").

    1. wait for the session file (written at claim: sandbox-runner R7)
    2. start the agent (SDK + bundled CLI) and report `ready` with the startup time
    3. long-poll the gateway; run one turn at a time, streaming events; honour interrupts
    4. stop on `end` or when the session's wall time is used up

Run: `python -m core` (the sandbox image's entrypoint). AGENT_SESSION_FILE overrides
the session file path (tests, local runs).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

import httpx

from core.types import SESSION_FILE, EventType, InterruptMessage, SessionFile, TurnMessage, TurnStatus
from core.gateway import EventSink, GatewayClient

logger = logging.getLogger("core")


class AgentLike(Protocol):
    last_result: dict[str, Any] | None     # the last turn's final figures (the SDK's ResultMessage)

    async def start(self) -> None: ...
    def run(self, text: str): ...          # async iterator of protocol-A event payloads
    async def interrupt(self) -> None: ...
    async def stop(self) -> None: ...


async def wait_for_session(path: Path, poll: float = 0.05) -> SessionFile:
    """A warm sandbox starts before its session exists; the file appears at claim."""
    logged = False
    while not path.is_file():
        if not logged:
            logger.info("waiting for %s", path)
            logged = True
        await asyncio.sleep(poll)
    return SessionFile.model_validate(json.loads(path.read_text()))


class Session:
    def __init__(self, session: SessionFile, gateway: GatewayClient, agent: AgentLike, sink: EventSink):
        self.s, self.gateway, self.agent, self.sink = session, gateway, agent, sink
        self._turn: asyncio.Task | None = None
        self._turn_id: str | None = None
        self._interrupted: set[str] = set()

    async def run(self, started: float) -> None:
        await self.agent.start()
        self.sink.emit({"type": EventType.RUNTIME_READY, "startup_ms": round((time.monotonic() - started) * 1000),
                        "profile": self.s.profile.name, "model": self.s.profile.model, "tools": self.s.profile.tools})
        deadline = time.monotonic() + self.s.profile.limits.wall_seconds
        backoff = 0.5
        try:
            while (remaining := deadline - time.monotonic()) > 0:
                try:
                    batch = await self.gateway.next_messages(wait=max(1, min(25, int(remaining))))
                    backoff = 0.5
                except httpx.HTTPError as exc:
                    logger.warning("gateway unreachable (%r); retrying in %.1fs", exc, backoff)
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 10)
                    continue
                for m in batch.messages:
                    if isinstance(m, TurnMessage):
                        self._start_turn(m)
                    elif isinstance(m, InterruptMessage):
                        await self._interrupt(m.turn_id)
                    else:                                   # end
                        return
            self.sink.emit({"type": EventType.RUNTIME_ERROR, "message": "session wall time used up"}, self._turn_id)
        finally:
            if self._turn and not self._turn.done():
                await self._interrupt(self._turn_id or "")
                await asyncio.wait({self._turn}, timeout=5)
            await self.agent.stop()
            await self.sink.close()

    def _start_turn(self, m: TurnMessage) -> None:
        if self._turn and not self._turn.done():
            self.sink.emit({"type": EventType.RUNTIME_ERROR, "message": "busy: a turn is already running"}, m.turn_id)
            self.sink.emit({"type": EventType.TURN_COMPLETED, "status": TurnStatus.FAILED, "reason": "busy",
                            "duration_ms": 0}, m.turn_id)
            return
        self._turn_id = m.turn_id
        self._turn = asyncio.create_task(self._run_turn(m))
        self._turn.add_done_callback(lambda t, turn_id=m.turn_id: self._turn_done(t, turn_id))

    async def _interrupt(self, turn_id: str) -> None:
        if self._turn and not self._turn.done() and turn_id == self._turn_id:
            self._interrupted.add(turn_id)
            await self.agent.interrupt()

    async def _run_turn(self, m: TurnMessage) -> None:
        t0 = time.monotonic()
        first_text_ms: int | None = None
        status, reason = TurnStatus.COMPLETED, None
        self.agent.last_result = None
        self.sink.emit({"type": EventType.TURN_STARTED}, m.turn_id)
        try:
            async for payload in self.agent.run(m.text):
                if payload["type"] == EventType.TEXT_DELTA and first_text_ms is None:
                    first_text_ms = round((time.monotonic() - t0) * 1000)
                self.sink.emit(payload, m.turn_id)
        except Exception as exc:  # the turn fails; the session goes on
            logger.exception("turn %s failed", m.turn_id)
            status, reason = TurnStatus.FAILED, type(exc).__name__
            self.sink.emit({"type": EventType.RUNTIME_ERROR, "message": f"{type(exc).__name__}: {exc}"}, m.turn_id)
        result = self.agent.last_result or {}
        if m.turn_id in self._interrupted:
            status, reason = TurnStatus.INTERRUPTED, None
        elif result.get("is_error"):
            status, reason = TurnStatus.FAILED, result.get("subtype")
        self.sink.emit({"type": EventType.TURN_COMPLETED, "status": status, "reason": reason,
                        "first_text_ms": first_text_ms, "duration_ms": round((time.monotonic() - t0) * 1000),
                        **{k: result.get(k) for k in ("stop_reason", "num_steps", "usage", "total_cost_usd")}},
                       m.turn_id)

    def _turn_done(self, task: asyncio.Task, turn_id: str) -> None:
        """A turn task must never die silently: log it and still end the turn for the caller."""
        if task.cancelled() or task.exception() is None:
            return
        logger.error("turn %s crashed", turn_id, exc_info=task.exception())
        self.sink.emit({"type": EventType.RUNTIME_ERROR, "message": f"runtime error: {type(task.exception()).__name__}"}, turn_id)
        self.sink.emit({"type": EventType.TURN_COMPLETED, "status": TurnStatus.FAILED, "reason": "runtime error",
                        "duration_ms": 0}, turn_id)


async def main(session_path: Path, agent_factory: Callable[[SessionFile], AgentLike] | None = None,
               http: httpx.AsyncClient | None = None) -> None:
    session = await wait_for_session(session_path)
    started = time.monotonic()           # startup_ms counts from the claim (the file appearing), not the warm wait
    if agent_factory is None:
        from core.agent import Agent
        agent_factory = Agent
    gateway = GatewayClient(session.gateway_url, session.capability, http=http)
    sink = EventSink(gateway, session.session_id)
    sink.start()
    try:
        await Session(session, gateway, agent_factory(session), sink).run(started)
    finally:
        await gateway.aclose()


def cli() -> None:
    logging.basicConfig(level=os.environ.get("AGENT_LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(name)s %(levelname)s %(message)s", stream=sys.stderr)
    asyncio.run(main(Path(os.environ.get("AGENT_SESSION_FILE", SESSION_FILE))))
