"""The voice turn with fakes for the mic, the speaker, speech-service and agent-service: the phases
Hey Pi sees, what flows between the hops, and how a turn ends (cancel, failures)."""

import asyncio
import json

import httpx

from app.voice.agent import AgentClient
from app.voice.speech import Heard, SpeechError
from app.voice.turn import VoiceAssistant, VoiceOptions


class UI:
    def __init__(self):
        self.phases, self.deltas, self.all = [], [], []

    async def phase(self, turn_id, phase, transcript=None):
        self.all.append((phase, transcript))
        if not self.phases or self.phases[-1][0] != phase:          # the phases, each once in a row
            self.phases.append((phase, transcript))

    async def reply_delta(self, turn_id, text):
        self.deltas.append(text)


class Mic:
    def __init__(self):
        self.opened = self.closed = 0

    async def frames(self):
        self.opened += 1
        try:
            while True:
                await asyncio.sleep(0.001)
                yield b"\0" * 3200
        finally:
            self.closed += 1


class Speaker:
    def __init__(self):
        self.audio, self.drained, self.stopped = b"", False, False

    async def open(self):
        pass

    async def write(self, pcm):
        self.audio += pcm

    async def drain(self):
        self.drained = True

    async def stop(self):
        self.stopped = True


class Speech:
    def __init__(self, heard="what's the weather", wait_commit=False, speak_error=None):
        self.heard, self.wait_commit, self.speak_error = heard, wait_commit, speak_error
        self.frames_seen, self.spoken = 0, []

    async def transcribe(self, frames, *, commit, silence_ms, language=None, on_partial=None):
        async for _ in frames:
            self.frames_seen += 1
            if self.frames_seen == 2 and on_partial:
                await on_partial("what's")
            if (commit.is_set() if self.wait_commit else self.frames_seen >= 5):
                break
        return Heard(self.heard, "English", "commit" if self.wait_commit else "silence")

    async def speak(self, texts, *, voice, language, on_audio):
        async for text in texts:
            if self.speak_error:
                raise self.speak_error
            self.spoken.append(text)
            await on_audio(text.encode())


class Agent:
    def __init__(self, deltas=("Sunny, ", "and warm."), hang=False):
        self.deltas, self.hang = deltas, hang
        self.prepared = self.cancelled = 0
        self.asked = []

    async def prepare(self):
        self.prepared += 1

    async def turn(self, text):
        self.asked.append(text)
        yield {"type": "turn.started"}
        for d in self.deltas:
            yield {"type": "message.delta", "text": d}
        if self.hang:
            await asyncio.sleep(3600)
        yield {"type": "message.completed", "text": "".join(self.deltas)}
        yield {"type": "turn.completed", "status": "completed"}

    async def cancel(self):
        self.cancelled += 1


def assistant(speech=None, agent=None):
    ui, mic, speaker = UI(), Mic(), Speaker()
    speech, agent = speech or Speech(), agent or Agent()
    va = VoiceAssistant(speech, agent, mic, lambda: speaker, ui, VoiceOptions())
    return va, ui, mic, speaker, speech, agent


async def finish(va):
    await asyncio.wait_for(asyncio.wait([va._task]), 5)


def test_a_spoken_turn_end_to_end():
    async def run():
        va, ui, mic, speaker, speech, agent = assistant()
        await va.listen("t1")
        await finish(va)
        assert [p for p, _ in ui.phases] == ["listening", "thinking", "speaking", "idle"]
        assert ("listening", "what's") in ui.all                  # a partial, shown while you talk
        assert ("thinking", "what's the weather") in ui.all       # the transcript
        assert agent.prepared == 1 and agent.asked == ["what's the weather"]
        assert ui.deltas == ["Sunny, ", "and warm."]
        assert "".join(speech.spoken).strip() == "Sunny, and warm."  # the agent's text into the TTS
        assert speaker.audio and speaker.drained and not speaker.stopped
        assert mic.opened == mic.closed == 1                         # the mic closed after the turn's end
    asyncio.run(run())


def test_a_typed_turn_skips_the_mic():
    async def run():
        va, ui, mic, speaker, speech, agent = assistant()
        await va.reply("t1", "hello")
        await finish(va)
        assert [p for p, _ in ui.phases] == ["thinking", "speaking", "idle"]
        assert mic.opened == 0 and agent.asked == ["hello"] and speaker.drained
    asyncio.run(run())


def test_nothing_heard_goes_back_to_idle():
    async def run():
        va, ui, mic, speaker, speech, agent = assistant(speech=Speech(heard="  "))
        await va.listen("t1")
        await finish(va)
        assert [p for p, _ in ui.phases] == ["listening", "idle"] and agent.asked == []
    asyncio.run(run())


def test_commit_ends_the_turn():
    async def run():
        va, ui, mic, speaker, speech, agent = assistant(speech=Speech(wait_commit=True))
        await va.listen("t1")
        await asyncio.sleep(0.05)
        assert va.busy and ui.phases[-1][0] == "listening"           # still listening: no commit yet
        va.commit()
        await finish(va)
        assert agent.asked == ["what's the weather"] and ui.phases[-1][0] == "idle"
    asyncio.run(run())


def test_cancel_while_the_agent_writes():
    async def run():
        va, ui, mic, speaker, speech, agent = assistant(agent=Agent(hang=True))
        await va.reply("t1", "tell me a long story")
        await asyncio.sleep(0.05)
        await va.cancel()
        assert agent.cancelled == 1 and speaker.stopped and not speaker.drained
        assert ui.phases[-1][0] == "idle" and not va.busy
    asyncio.run(run())


def test_a_new_turn_cancels_the_last():
    async def run():
        va, ui, mic, speaker, speech, agent = assistant(agent=Agent(hang=True))
        await va.reply("t1", "first")
        await asyncio.sleep(0.05)
        agent.hang = False
        await va.reply("t2", "second")
        await finish(va)
        assert agent.cancelled == 1 and agent.asked == ["first", "second"]
    asyncio.run(run())


def test_the_speech_failing_ends_the_turn_and_the_agents():
    async def run():
        va, ui, mic, speaker, speech, agent = assistant(speech=Speech(speak_error=SpeechError("speech: refused, 503")),
                                                        agent=Agent(hang=True))
        await va.reply("t1", "hello")
        await finish(va)                                             # ends by itself, logged
        assert agent.cancelled == 1 and speaker.stopped and ui.phases[-1][0] == "idle"
    asyncio.run(run())


# --- agent-service's client ---------------------------------------------------------------------------

def agent_service(log):
    """A fake agent-service: sessions, and turns that stream two deltas. A session id in `ended` is over."""
    state = {"n": 0, "ended": set()}

    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = json.loads(request.content or b"{}")
        log.append((request.method, path, body))
        if path == "/v1/sessions":
            state["n"] += 1
            conversation = body.get("conversation_id") or f"c{state['n']}"
            return httpx.Response(201, json={"session_id": f"s{state['n']}", "conversation_id": conversation, "state": "starting"})
        session = path.split("/")[3]
        if session in state["ended"]:
            return httpx.Response(409, json={"title": "the session has ended"})
        events = [{"type": "message.delta", "text": "Hi"}, {"type": "turn.completed", "status": "completed"}]
        sse = "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)
        return httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})

    return handle, state


def client(log, now):
    handle, state = agent_service(log)

    async def token():
        return "t"

    http = httpx.AsyncClient(transport=httpx.MockTransport(handle), base_url="http://agent")
    return AgentClient("http://agent", token, "voice", 600, http=http, clock=lambda: now[0]), state


async def all_of(agen):
    return [e async for e in agen]


def test_agent_keeps_the_conversation_then_starts_a_new_one_after_quiet():
    async def run():
        log, now = [], [0.0]
        agent, _ = client(log, now)
        assert [e["type"] for e in await all_of(agent.turn("one"))] == ["message.delta", "turn.completed"]
        now[0] = 300
        await all_of(agent.turn("two"))                              # same session, same conversation
        assert [p for _, p, _ in log].count("/v1/sessions") == 1
        now[0] = 300 + 601                                           # 10 minutes of quiet
        await all_of(agent.turn("three"))
        starts = [b for _, p, b in log if p == "/v1/sessions"]
        assert starts[-1] == {"profile": "voice"}                    # a new conversation, not the old one
    asyncio.run(run())


def test_agent_replaces_an_ended_session_in_the_same_conversation():
    async def run():
        log, now = [], [0.0]
        agent, state = client(log, now)
        await all_of(agent.turn("one"))
        state["ended"].add(agent.session_id)
        events = await all_of(agent.turn("two"))
        assert events[-1]["type"] == "turn.completed"
        starts = [b for _, p, b in log if p == "/v1/sessions"]
        assert starts[-1] == {"profile": "voice", "conversation_id": "c1"}
        assert agent.session_id == "s2"
    asyncio.run(run())
