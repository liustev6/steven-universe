"""Fakes shared by the tests: the runner, and a sandbox's runtime."""

import asyncio
import uuid
from pathlib import Path

from app.profiles import load_profiles
from app.sessions.runner import NoSandbox
from core.types import build_event

PROFILES = load_profiles(Path(__file__).resolve().parents[4] / "infra/services/agent-service/profiles.yaml")
ME, OTHER = "service:agent-cli", "service:smoke-test"


class FakeRunner:
    def __init__(self):
        self.claims: list[dict] = []
        self.released: list[uuid.UUID] = []
        self.pods: set[uuid.UUID] = set()
        self.no_sandbox = False
        self.unreachable = False
        self.claim_delay = 0.0

    async def claim(self, template, session_id, capability, profile):
        await asyncio.sleep(self.claim_delay)
        if self.no_sandbox:
            raise NoSandbox(7)
        sandbox = uuid.uuid4()
        self.claims.append({"template": template, "session_id": session_id, "capability": capability,
                            "profile": profile, "sandbox_id": sandbox})
        self.pods.add(sandbox)
        return sandbox

    async def release(self, sandbox_id):
        self.released.append(sandbox_id)
        self.pods.discard(sandbox_id)

    async def claimed(self):
        return None if self.unreachable else set(self.pods)


class Runtime:
    """What the sandbox's runtime would send, numbered like its EventSink."""

    def __init__(self, session_id):
        self.session_id, self.seq = session_id, 0

    def __call__(self, payload, turn=None):
        e = build_event(payload, session_id=str(self.session_id), turn_id=str(turn) if turn else None,
                        seq=self.seq, ts=1.0)
        self.seq += 1
        return e.model_dump(mode="json")

    def ready(self):
        return self({"type": "runtime.ready", "startup_ms": 400, "profile": "x", "model": None, "tools": []})

    def answer(self, turn, text="Hello", total=100):
        return [self({"type": "turn.started"}, turn), self({"type": "text.delta", "text": text}, turn),
                self({"type": "text.completed", "text": text}, turn),
                self({"type": "turn.completed", "status": "completed", "duration_ms": 50,
                      "usage": {"total": {"input_tokens": total - 10, "cached_input_tokens": 0, "output_tokens": 10,
                                          "reasoning_output_tokens": 0, "total_tokens": total}}}, turn)]
