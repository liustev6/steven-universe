"""The real runtime loop against the real dev gateway (in-process), with a fake agent instead of
the SDK: turns stream, interrupts stop a turn, a second turn while busy is refused, end stops it."""

import asyncio
import json

import httpx
import pytest

from agent_dev.gateway import Settings, create_app
from core.main import main


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


@pytest.fixture
def settings(tmp_path):
    (tmp_path / "key").write_text("sk-real-key")
    (tmp_path / "token").write_text("dev-token")
    (tmp_path / "p.yaml").write_text("name: voice\nlimits: {wall_seconds: 60}\n")
    return Settings(profile_file=tmp_path / "p.yaml", session_dir=tmp_path / "session", model_auth="apikey",
                    api_key_file=tmp_path / "key", dev_token_file=tmp_path / "token", gateway_url="http://gw:8080")


def lines(r):
    return [json.loads(x) for x in r.text.splitlines() if x.strip()]


def test_end_to_end(settings):
    app = create_app(settings)

    async def go():
        transport = httpx.ASGITransport(app=app)
        async with app.router.lifespan_context(app), \
                httpx.AsyncClient(transport=transport, base_url="http://gw:8080") as runtime_http, \
                httpx.AsyncClient(transport=transport, base_url="http://gw:8080",
                                  headers={"Authorization": "Bearer dev-token"}) as you:
            runtime = asyncio.create_task(main(settings.session_dir / "session.json", agent_factory=FakeAgent, http=runtime_http))

            # 1. A turn streams to its result.
            ev = lines(await you.post("/dev/turns", json={"text": "tool hello there"}))
            kinds = [e["type"] for e in ev]
            assert kinds[0] == "dev.accepted" and kinds[1] == "turn.started" and kinds[-1] == "turn.completed"
            assert "".join(e["text"] for e in ev if e["type"] == "text.delta") == "tool hello there "
            assert "tool.started" in kinds and "tool.completed" in kinds
            assert ev[-1]["status"] == "completed" and ev[-1]["first_text_ms"] is not None
            assert ev[-1]["usage"] == {"total": {"output_tokens": 6}} and ev[-1]["v"] == 1
            seqs = [e["seq"] for e in ev[1:]]
            assert seqs == sorted(seqs)

            # 2. An interrupt stops a long turn; a turn sent meanwhile is refused as busy.
            long_turn = asyncio.create_task(you.post("/dev/turns", json={"text": "slow " * 20}))
            while not any(e["type"] == "text.delta" and e["turn_id"] == "t2" for e in app.state.dev.events):
                await asyncio.sleep(0.05)
            busy = lines(await you.post("/dev/turns", json={"text": "meanwhile"}))
            assert busy[-1]["type"] == "turn.completed" and busy[-1]["status"] == "failed" and busy[-1]["reason"] == "busy"
            await you.post("/dev/turns/t2/interrupt")
            ev2 = lines(await long_turn)
            assert ev2[-1]["status"] == "interrupted"
            assert sum(e["type"] == "text.delta" for e in ev2) < 20

            # 3. The runtime reported ready once, then ends on request.
            assert [e["type"] for e in app.state.dev.events].count("runtime.ready") == 1
            await you.post("/dev/end")
            await asyncio.wait_for(runtime, 5)

    asyncio.run(go())
