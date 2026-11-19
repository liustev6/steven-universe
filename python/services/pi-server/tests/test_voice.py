"""The voice assistant with fakes for the mic's frames, the wake word, the voice detector, the
speaker, speech-service and agent-service; a fake clock (each frame is 100 ms). What's checked: the
session's life (wake word → waiting → turns → 5 s of quiet → released), what Hey Pi sees, what flows
between the hops, and how turns end (cancel, failures)."""

import asyncio
import json
from datetime import UTC, datetime

import httpx

from app.voice.agent import AgentClient, client_info
from app.voice.assistant import State, VoiceAssistant, VoiceOptions, main_language
from app.voice.speech import FLUSH, Heard, SpeechError
from app.voice.spoken import SpokenText

WAKE, SPEECH, QUIET = b"W" * 3200, b"S" * 3200, b"\0" * 3200
VOICE = (SPEECH,) * 3                   # 300 ms of speech in a row: what starts a turn


class UI:
    def __init__(self):
        self.phases, self.all, self.deltas, self.woke = [], [], [], 0

    async def phase(self, turn_id, phase, transcript=None):
        self.all.append((phase, transcript))
        if not self.phases or self.phases[-1] != phase:              # each phase once in a row
            self.phases.append(phase)

    async def reply_delta(self, turn_id, text):
        self.deltas.append(text)

    async def awake(self):
        self.woke += 1


class Wake:
    def __init__(self):
        self.resets = 0

    def feed(self, frame):
        return frame == WAKE

    def reset(self):
        self.resets += 1


class Voice:
    def feed(self, frame):
        return frame in (WAKE, SPEECH)


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
    """Hears until a quiet frame follows speech (the server's turn detection), or a commit."""

    def __init__(self, heard=("what's the weather",), fail=None, speak_error=None):
        self.heard, self.fail, self.speak_error = list(heard), fail, speak_error
        self.utterances, self.spoken, self.voices, self.contexts, self.flushes = [], [], [], [], []

    async def transcribe(self, frames, *, commit, silence_ms, language=None, context="", on_partial=None):
        self.contexts.append(context)
        if self.fail:
            raise self.fail
        got, spoke = [], False
        async for frame in frames:
            got.append(frame)
            spoke = spoke or frame == SPEECH
            if on_partial and len(got) == 2:
                await on_partial("what's")
            if commit.is_set() or (spoke and frame == QUIET):
                break
        self.utterances.append(got)
        return Heard(self.heard.pop(0) if self.heard else "", "English", "silence")

    async def speak(self, texts, *, voice, language, on_audio):
        self.voices.append(voice)
        async for text in texts:
            if self.speak_error:
                raise self.speak_error
            if text == FLUSH:                       # speech-service's flush: not text, a passage's end
                self.flushes.append(len(self.spoken))
                continue
            self.spoken.append(text)
            await on_audio(text.encode())


class Agent:
    def __init__(self, hang=False, deltas=("Sunny, ", "and warm.")):
        self.hang, self.deltas = hang, deltas
        self.started = self.ended = self.cancelled = 0
        self.asked = []

    async def start(self, conversation_id=None):
        self.started += 1
        return {"session_id": f"s{self.started}", "conversation_id": "c", "state": "starting"}

    async def turn(self, text):
        self.asked.append(text)
        for d in self.deltas:
            yield {"type": "message.completed"} if d is None else {"type": "message.delta", "text": d}
        if self.hang:
            await asyncio.sleep(3600)
        yield {"type": "turn.completed", "status": "completed"}

    async def cancel(self):
        self.cancelled += 1

    async def end(self):
        self.ended += 1


class Rig:
    def __init__(self, speech=None, agent=None):
        self.now = [0.0]
        self.ui, self.speaker, self.wake = UI(), Speaker(), Wake()
        self.speech, self.agent = speech or Speech(), agent or Agent()
        self.va = VoiceAssistant(self.speech, self.agent, mic=None, speaker=lambda: self.speaker, ui=self.ui,
                                 options=VoiceOptions(window=5.0, stt_context="Speaks English and Chinese."),
                                 wake=self.wake, voice=Voice(),
                                 clock=lambda: self.now[0])

    async def frames(self, *frames):
        for frame in frames:
            self.now[0] += 0.1
            await self.va.on_frame(frame)
            for _ in range(5):                                       # let the turn's tasks run
                await asyncio.sleep(0)

    async def settle(self):
        for task in (self.va._turn, self.va._ending):
            if task is not None:
                await asyncio.wait_for(asyncio.wait([task]), 5)
        for _ in range(5):
            await asyncio.sleep(0)


def run(coro):
    asyncio.run(coro)


def test_without_the_wake_word_nothing_happens():
    async def go():
        r = Rig()
        await r.frames(SPEECH, SPEECH, QUIET, SPEECH)
        assert r.va.state is State.IDLE and r.agent.started == 0 and r.ui.phases == []
    run(go())


def test_a_session_end_to_end():
    async def go():
        r = Rig()
        await r.frames(WAKE)
        assert r.va.state is State.WAITING and r.ui.woke == 1 and r.ui.phases == ["listening"]
        await r.frames(QUIET, QUIET, QUIET, *VOICE, QUIET)                 # a voice, then the turn ends
        await r.settle()
        assert r.agent.started == 1 and r.agent.asked == ["what's the weather"]
        assert r.ui.phases == ["listening", "thinking", "speaking", "listening"]
        assert ("thinking", "what's the weather") in r.ui.all and r.ui.deltas == ["Sunny, ", "and warm."]
        assert "".join(r.speech.spoken).strip() == "Sunny, and warm." and r.speaker.drained
        assert r.va.state is State.WAITING                            # 5 s more for a follow-up
        await r.frames(*[QUIET] * 51)                                 # nobody: the session ends
        await r.settle()
        assert r.va.state is State.IDLE and r.agent.ended == 1 and r.ui.phases[-1] == "idle"
    run(go())


def test_the_half_second_before_a_voice_goes_with_it():
    async def go():
        r = Rig()
        await r.frames(WAKE, QUIET, QUIET, QUIET, QUIET, QUIET, QUIET, *VOICE, QUIET)
        await r.settle()
        heard = r.speech.utterances[0]
        assert heard[:5] == [QUIET] * 2 + [SPEECH] * 3               # 200 ms before it, then the voice's 300
    run(go())


def test_a_follow_up_within_the_window_is_the_same_session():
    async def go():
        r = Rig(speech=Speech(heard=["what's the weather", "and tomorrow?"]))
        await r.frames(WAKE, QUIET, QUIET, QUIET, *VOICE, QUIET)
        await r.settle()
        await r.frames(QUIET, QUIET, QUIET, QUIET, *VOICE, QUIET)      # 0.5 s later: no wake word needed
        await r.settle()
        assert r.agent.asked == ["what's the weather", "and tomorrow?"] and r.agent.started == 1
        assert r.agent.ended == 0 and r.va.state is State.WAITING
    run(go())


def test_after_the_wake_word_nobody_speaks():
    async def go():
        r = Rig()
        await r.frames(WAKE, *[QUIET] * 51)
        await r.settle()
        assert r.agent.started == 1 and r.agent.ended == 1 and r.agent.asked == []
        assert r.ui.phases == ["listening", "idle"] and r.va.state is State.IDLE
    run(go())


def test_the_wake_word_itself_does_not_start_a_turn():
    async def go():
        r = Rig()
        await r.frames(WAKE, WAKE)                                   # the word's tail: inside the hold-off
        assert r.va.state is State.WAITING and r.speech.utterances == []
    run(go())


def test_the_mic_is_ignored_while_replying():
    async def go():
        r = Rig(agent=Agent(hang=True))
        await r.frames(WAKE, QUIET, QUIET, QUIET, *VOICE, QUIET)
        assert r.va.state is State.REPLYING
        await r.frames(SPEECH, WAKE, SPEECH, QUIET)
        assert len(r.speech.utterances) == 1 and r.va.state is State.REPLYING
    run(go())


def test_cancel_mid_reply_ends_everything():
    async def go():
        r = Rig(agent=Agent(hang=True))
        await r.frames(WAKE, QUIET, QUIET, QUIET, *VOICE, QUIET)
        await r.va.cancel()
        assert r.agent.cancelled == 1 and r.speaker.stopped and r.agent.ended == 1
        assert r.va.state is State.IDLE and r.ui.phases[-1] == "idle"
    run(go())


def test_push_to_talk_starts_a_turn_at_once_and_commit_ends_it():
    async def go():
        r = Rig()
        await r.va.listen()
        await asyncio.sleep(0)                                       # the claim runs in its own task
        assert r.va.state is State.LISTENING and r.agent.started == 1
        await r.frames(SPEECH, SPEECH)
        r.va.commit()
        await r.frames(SPEECH)
        await r.settle()
        assert r.agent.asked == ["what's the weather"] and r.va.state is State.WAITING
    run(go())


def test_a_typed_turn_is_spoken_in_the_session():
    async def go():
        r = Rig()
        await r.va.say("hello")
        await r.settle()
        assert r.agent.asked == ["hello"] and r.speaker.drained and r.va.state is State.WAITING
        assert r.ui.phases == ["listening", "thinking", "speaking", "listening"]
    run(go())


def test_a_failed_hop_ends_the_turn_not_the_session():
    async def go():
        r = Rig(speech=Speech(fail=SpeechError("transcription: refused, 503")))
        await r.frames(WAKE, QUIET, QUIET, QUIET, *VOICE)
        await r.settle()
        assert r.va.state is State.WAITING and r.agent.ended == 0
    run(go())


def test_the_speech_failing_mid_reply_stops_the_agent_too():
    async def go():
        r = Rig(speech=Speech(speak_error=SpeechError("speech: refused, 503")), agent=Agent(hang=True))
        await r.va.say("hello")
        await r.settle()
        assert r.agent.cancelled == 1 and r.speaker.stopped and r.va.state is State.WAITING
    run(go())


# --- agent-service's client ---------------------------------------------------------------------------

def agent_service(log):
    """A fake agent-service: sessions, and turns that stream a delta. A session in `ended` is over."""
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
        if request.method == "DELETE":
            state["ended"].add(session)
            return httpx.Response(200, json={"ended": True})
        if session in state["ended"]:
            return httpx.Response(409, json={"title": "the session has ended"})
        events = [{"type": "message.delta", "text": "Hi"}, {"type": "turn.completed", "status": "completed"}]
        sse = "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)
        return httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})

    return handle, state


def client(log, instructions=None):
    handle, state = agent_service(log)

    async def token():
        return "t"

    http = httpx.AsyncClient(transport=httpx.MockTransport(handle), base_url="http://agent")
    return AgentClient("http://agent", token, "pi", http=http, instructions=instructions), state


async def all_of(agen):
    return [e async for e in agen]


def test_each_voice_session_is_a_new_conversation_released_at_its_end():
    async def go():
        log = []
        agent, state = client(log)
        await agent.start()
        assert [e["type"] for e in await all_of(agent.turn("one"))] == ["message.delta", "turn.completed"]
        await agent.end()
        assert ("DELETE", "/v1/sessions/s1", {}) in log and agent.session_id is None
        await agent.start()
        starts = [b for m, p, b in log if p == "/v1/sessions"]
        assert starts == [{"profile": "pi"}, {"profile": "pi"}]   # a new conversation each time
    run(go())


def test_an_ended_session_is_replaced_in_the_same_conversation():
    async def go():
        log = []
        agent, state = client(log)
        await agent.start()
        state["ended"].add(agent.session_id)                         # e.g. its wall time
        events = await all_of(agent.turn("two"))
        assert events[-1]["type"] == "turn.completed" and agent.session_id == "s2"
        assert [b for _, p, b in log if p == "/v1/sessions"][-1] == {"profile": "pi", "conversation_id": "c1"}
    run(go())


def test_the_speech_is_read_at_once_while_the_speaker_plays_on_its_own():
    """The TTS session ends when its audio is made, not when it has played (it holds a slot)."""
    async def go():
        r = Rig()
        released = asyncio.Event()
        order = []

        async def slow_write(pcm):                                   # a speaker that plays in real time
            order.append("write")
            await released.wait()

        r.speaker.write = slow_write
        spoken = r.speech.speak

        async def speak(*a, **kw):
            await spoken(*a, **kw)
            order.append("speech done")

        r.speech.speak = speak
        await r.va.say("hello")
        for _ in range(20):
            await asyncio.sleep(0)
        assert "speech done" in order and r.va.state is State.REPLYING   # made; still playing
        released.set()
        await r.settle()
        assert r.va.state is State.WAITING
    run(go())


def test_the_reply_speaks_in_the_turns_main_language():
    assert main_language("今天天气怎么样？") == "Chinese"
    assert main_language("What's the weather?") == "English"
    assert main_language("我昨天用iPhone给Steven发了一条message。") == "Chinese"    # mostly Chinese
    assert main_language("turn off the 客厅 lights please") == "English"           # mostly English
    assert main_language("好 ok", heard="Chinese") == "Chinese"                     # a tie: what was heard
    assert main_language("好 ok") == "English"
    o = VoiceOptions()
    assert (o.voice_for("关灯"), o.voice_for("Turn off the lights")) == ("dylan", "ryan")


def test_the_sessions_first_turn_picks_its_voice_for_the_whole_session():
    async def go():
        r = Rig(speech=Speech(heard=["今天星期几？", "What time is it?"]))
        turn = (QUIET, QUIET, QUIET, *VOICE, QUIET)
        await r.frames(WAKE, *turn)
        await r.settle()
        await r.frames(*turn)                                        # English now: still dylan
        await r.settle()
        await r.va.say("And tomorrow?")
        await r.settle()
        assert r.speech.voices == ["dylan", "dylan", "dylan"]
        await r.frames(*[QUIET] * 51)                                # the session ends
        await r.settle()
        await r.va.say("Hello")                                      # a new session, started in English
        await r.settle()
        assert r.speech.voices[-1] == "ryan"
    run(go())


def test_speech_to_text_hears_with_the_sessions_last_two_turns():
    async def go():
        r = Rig(speech=Speech(heard=["what's the weather", "Seattle downtown", "and tomorrow?", "thanks"]))
        turn = (QUIET, QUIET, QUIET, *VOICE, QUIET)
        await r.frames(WAKE, *turn)
        await r.settle()
        for _ in range(3):
            await r.frames(*turn)
            await r.settle()
        first, second, third, fourth = r.speech.contexts
        assert first == "Speaks English and Chinese."                    # nothing said yet: the speakers' hint
        assert all(c.startswith("Speaks English and Chinese.") for c in r.speech.contexts)
        assert "User: what's the weather" in second and "Assistant: Sunny, and warm." in second
        assert "Seattle downtown" in third
        assert "what's the weather" not in fourth and "Seattle downtown" in fourth   # the last two only
        await r.frames(*[QUIET] * 51)                                    # the session ends
        await r.settle()
        assert r.va.context() == "Speaks English and Chinese."           # a new session starts clean
    run(go())


def test_the_client_info_goes_with_every_start():
    winter = datetime(2026, 11, 18, 10, 55, tzinfo=UTC)
    assert client_info("Seattle, United States", "America/Los_Angeles", winter) == (
        "Additional Client Info:\n"
        "timezone: America/Los_Angeles\n"
        "City, Country: Seattle, United States\n"
        "now: 2026-11-18 02:55 (Wednesday, UTC-0800)")
    assert client_info("", "", winter) == "Additional Client Info:\nnow: 2026-11-18 10:55 (Wednesday, UTC+0000)"

    async def go():
        log, told = [], iter(["info 1", "info 2"])
        agent, state = client(log, instructions=lambda: next(told))
        await agent.start()
        state["ended"].add(agent.session_id)
        await all_of(agent.turn("again"))                            # replaced: told again, built anew
        starts = [b for _, p, b in log if p == "/v1/sessions"]
        assert starts == [{"profile": "pi", "instructions": "info 1"},
                          {"profile": "pi", "conversation_id": "c1", "instructions": "info 2"}]
    run(go())


def test_spoken_text_drops_citations_and_markdown_even_split():
    t = SpokenText()
    pieces = ["Mostly **cloudy**, high near 50. \ue200ci", "te\ue202turn0fore", "cast0\ue201 Bring `an` umbrella."]
    assert "".join(t.feed(p) for p in pieces) == "Mostly cloudy, high near 50.  Bring an umbrella."
    assert t.feed("明天多云，最高约十度。") == "明天多云，最高约十度。"                  # Chinese untouched
    t.feed("half a marker \ue200cite\ue202tur")
    t.end()                                                           # the message ended: dropped, not carried
    assert t.feed("Next message.") == "Next message."


def test_a_search_answer_is_spoken_without_its_citation():
    async def go():
        r = Rig(agent=Agent(deltas=("Let me check.", None, "Cloudy, about 10 degrees. \ue200cite\ue202turn0",
                                    "forecast0\ue201")))
        await r.frames(WAKE, QUIET, QUIET, QUIET, *VOICE, QUIET)
        await r.settle()
        said = " ".join(r.speech.spoken)
        assert said.split() == ["Let", "me", "check.", "Cloudy,", "about", "10", "degrees."]
        first = "".join(r.speech.spoken[:r.speech.flushes[0]])           # flushed at its message's end, before the search
        assert first.split() == ["Let", "me", "check."]
        assert "turn0" not in "".join(r.ui.deltas) and "\ue200" not in said
    run(go())


def test_a_short_burst_is_not_a_voice():
    async def go():
        r = Rig()
        await r.frames(WAKE, QUIET, QUIET, QUIET, SPEECH, SPEECH, QUIET, SPEECH, QUIET)   # 200 ms, then 100
        assert r.va.state is State.WAITING and r.speech.utterances == []
        await r.frames(*VOICE)                                        # 300 ms in a row: a turn
        assert r.va.state is not State.WAITING
    run(go())


class Endless(Speech):
    """Never hears silence (a fan): only a commit ends the turn."""

    async def transcribe(self, frames, *, commit, silence_ms, language=None, context="", on_partial=None):
        got = []
        async for frame in frames:
            got.append(frame)
            if commit.is_set():
                break
        self.utterances.append(got)
        return Heard(self.heard.pop(0) if self.heard else "", "English", "commit")


def test_the_first_turn_is_sent_as_heard_at_the_cap_then_the_fan_ends_the_session():
    async def go():
        r = Rig(speech=Endless(heard=["what's the weather", "嘿派"]))
        await r.frames(WAKE, QUIET, QUIET, QUIET, *VOICE)
        await r.frames(*[SPEECH] * 160)                               # a fan: 16 s, never silent
        assert r.agent.asked == ["what's the weather"]                # asked with the wake word: answered at 15 s
        assert len(r.speech.utterances[0]) == 5 + 150 + 1               # the pre-roll, 15 s, the frame that sees the commit
        await r.frames(*[SPEECH] * 160)                               # the fan goes on: a "follow-up"
        await r.settle()
        assert r.agent.asked == ["what's the weather"]                # ... dropped, never sent
        assert r.va.state is State.IDLE and r.agent.ended == 1
    run(go())


def test_a_follow_up_without_silence_is_noise_and_ends_the_session():
    async def go():
        r = Rig(speech=Endless(heard=["what's the weather", "嘿派"]))
        await r.frames(WAKE, QUIET, QUIET, QUIET, *VOICE)
        await r.frames(*[SPEECH] * 2)
        r.va.commit()                                                 # the first turn, answered
        await r.frames(SPEECH)
        await r.settle()
        assert r.agent.asked == ["what's the weather"] and r.va.state is State.WAITING
        await r.frames(QUIET, QUIET, QUIET, QUIET, *[SPEECH] * 160)   # the fan: a "follow-up" never silent
        await r.settle()
        assert r.agent.asked == ["what's the weather"]                # not sent to the agent
        assert r.va.state is State.IDLE and r.agent.ended == 1 and r.ui.phases[-1] == "idle"
    run(go())
