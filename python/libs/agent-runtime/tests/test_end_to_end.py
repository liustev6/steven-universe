"""The real runtime loop against an in-process stand-in for agent-service's gateway, with a fake agent
instead of the SDK: turns stream, interrupts stop a turn, a second turn while busy is refused, end
stops it. (agent-service's own gateway has its tests in python/services/agent-service.)"""

import asyncio

import httpx
import pytest
from fastapi import FastAPI, HTTPException, Request

from core.main import main
from core.types import EventBatch, Limits, Profile, SessionFile, parse_event

CAPABILITY = "c" * 43


class FakeAgent:
    """Speaks the words of the prompt one by one; 'slow' makes it take long enough to interrupt."""

    def __init__(self, session):
        self.interrupted = asyncio.Event()
        self.started = False
        self.last_result = None

    async def start(self):
        self.started = True

    async def run(self, text):
        for word in text.split():
            if self.interrupted.is_set():
                break
            await asyncio.sleep(0.5 if "slow" in text else 0.01)
            yield {"type": "text.delta", "text": word + " "}
        if text.startswith("tool"):
            yield {"type": "tool.started", "tool_use_id": "t", "name": "Read", "input": {"file_path": "/a"}}
            yield {"type": "tool.completed", "tool_use_id": "t", "is_error": False, "output": "ok"}
        self.interrupted.clear()
        # What the real Translator reports from Codex's turn/completed.
        self.last_result = {"is_error": False, "subtype": None, "usage": {"total": {"output_tokens": 6}}}

    async def interrupt(self):
        self.interrupted.set()

    async def stop(self):
        pass


class Gateway:
    """The gateway's side of protocol A for one session, in memory: the session file, the long
    poll (GET /v1/runtime/turns), events in (POST /v1/runtime/events)."""

    def __init__(self, session_dir):
        session_dir.mkdir()
        self.session_file = session_dir / "session.json"
        self.session_file.write_text(SessionFile(
            session_id="s1", capability=CAPABILITY, gateway_url="http://gw",
            profile=Profile(name="voice", limits=Limits(wall_seconds=60))).model_dump_json())
        self.outbox: asyncio.Queue = asyncio.Queue()
        self.events: list[dict] = []
        self.app = FastAPI()

        def check(request: Request) -> None:
            if request.headers.get("authorization") != f"Bearer {CAPABILITY}":
                raise HTTPException(401)

        @self.app.get("/v1/runtime/turns")
        async def turns(request: Request, wait: float = 25):
            check(request)
            try:
                return {"messages": [await asyncio.wait_for(self.outbox.get(), wait)]}
            except TimeoutError:
                return {"messages": []}

        @self.app.post("/v1/runtime/events")
        async def events_in(request: Request, batch: EventBatch):
            check(request)
            self.events += [parse_event(e).model_dump(mode="json") for e in batch.events]
            return {"ok": True}

    def send(self, message: dict) -> None:
        self.outbox.put_nowait(message)

    async def turn_events(self, turn_id: str) -> list[dict]:
        """This turn's events, once its turn.completed has arrived."""
        while not any(e["type"] == "turn.completed" and e["turn_id"] == turn_id for e in self.events):
            await asyncio.sleep(0.02)
        return [e for e in self.events if e["turn_id"] == turn_id]


@pytest.fixture
def gateway(tmp_path):
    return Gateway(tmp_path / "session")


def test_end_to_end(gateway):
    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway.app), base_url="http://gw") as http:
            runtime = asyncio.create_task(main(gateway.session_file, agent_factory=FakeAgent, http=http))

            # 1. A turn streams to its result.
            gateway.send({"type": "turn", "turn_id": "t1", "text": "tool hello there"})
            ev = await asyncio.wait_for(gateway.turn_events("t1"), 5)
            kinds = [e["type"] for e in ev]
            assert kinds[0] == "turn.started" and kinds[-1] == "turn.completed"
            assert "".join(e["text"] for e in ev if e["type"] == "text.delta") == "tool hello there "
            assert "tool.started" in kinds and "tool.completed" in kinds
            assert ev[-1]["status"] == "completed" and ev[-1]["first_text_ms"] is not None
            assert ev[-1]["usage"] == {"total": {"output_tokens": 6}} and ev[-1]["v"] == 1
            seqs = [e["seq"] for e in ev]
            assert seqs == sorted(seqs)

            # 2. An interrupt stops a long turn; a turn sent meanwhile is refused as busy.
            gateway.send({"type": "turn", "turn_id": "t2", "text": "slow " * 20})
            while not any(e["type"] == "text.delta" and e["turn_id"] == "t2" for e in gateway.events):
                await asyncio.sleep(0.05)
            gateway.send({"type": "turn", "turn_id": "t3", "text": "meanwhile"})
            busy = await asyncio.wait_for(gateway.turn_events("t3"), 5)
            assert busy[-1]["status"] == "failed" and busy[-1]["reason"] == "busy"
            gateway.send({"type": "interrupt", "turn_id": "t2"})
            ev2 = await asyncio.wait_for(gateway.turn_events("t2"), 5)
            assert ev2[-1]["status"] == "interrupted"
            assert sum(e["type"] == "text.delta" for e in ev2) < 20

            # 3. The runtime reported ready once, then ends on request.
            assert [e["type"] for e in gateway.events].count("runtime.ready") == 1
            gateway.send({"type": "end"})
            await asyncio.wait_for(runtime, 5)

    asyncio.run(go())
