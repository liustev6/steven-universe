"""The API (app/api) over the real work threads with fake models (no GPU), and a fake auth: a token
"Bearer scope1 scope2" grants those scopes."""

import time

import numpy as np
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from service_auth import Principal
from starlette.testclient import WebSocketDenialResponse

from app.api import create_api
from app.config import Settings
from app.service import Service
from app.stt_engine.batch import STTBatch
from app.tts_engine.chunker import SAMPLES_PER_FRAME as SPF
from app.work.gpu import NoGPU
from app.work.stt_worker import STTWorker
from app.work.tts_worker import TTSWorker
from tests.test_stt_batch import FakeEngine
from tests.test_workers import FakeReplyText, FakeTTSBatch


class FakeAuth:
    async def authenticate(self, conn):
        header = conn.headers.get("authorization", "")
        if not header.startswith("Bearer "):
            raise HTTPException(401, "authentication required", headers={"WWW-Authenticate": "Bearer"})
        return Principal(service="test", scopes=frozenset(header[7:].split()))

    def require(self, *scopes):
        from fastapi import Request

        async def dependency(request: Request):
            p = await self.authenticate(request)
            if not p.has_scopes(*scopes):
                raise HTTPException(403, "insufficient scope")
            return p
        return dependency


ALL = {"authorization": "Bearer speech:create speech:transcribe status:read"}


def make(slots=2, warm=True, **settings):
    s = Settings(TTS_SLOTS=slots, STT_SLOTS=slots, QUEUE_WAIT_SECONDS=0.5, TTS_IDLE_SECONDS=0.5,
                 STT_IDLE_SECONDS=0.5, **settings)
    tts = TTSWorker(FakeTTSBatch(slots), text=FakeReplyText(),
                    decode=lambda frames: np.zeros(len(frames) * SPF, dtype=np.float32), gpu=NoGPU())
    stt = STTWorker(STTBatch(FakeEngine(), slots=slots), tick=0.05)
    tts.start(); stt.start()
    service = Service(tts, stt, voices={"ryan": "English", "vivian": "Chinese"}, languages=["English", "Chinese"],
                      settings=s, warm=warm)
    return TestClient(create_api(service, FakeAuth())), service


def drain(ws, with_code=False):
    """Messages until the server closes: (json messages, total audio bytes[, the close code])."""
    msgs, audio = [], 0
    while True:
        m = ws.receive()
        if m["type"] == "websocket.close":
            return (msgs, audio, m.get("code")) if with_code else (msgs, audio)
        if m.get("bytes") is not None:
            audio += len(m["bytes"])
        elif m.get("text") is not None:
            import json
            msgs.append(json.loads(m["text"]))


def denied(client, url, headers):
    with pytest.raises(WebSocketDenialResponse) as e:
        with client.websocket_connect(url, headers=headers):
            pass
    return e.value


# ---- handshakes --------------------------------------------------------------------------------------
def test_handshakes_are_refused_with_http_statuses():
    client, service = make()
    assert denied(client, "/v1/realtime/speech?voice=ryan", {}).status_code == 401
    assert denied(client, "/v1/realtime/speech?voice=ryan", {"authorization": "Bearer status:read"}).status_code == 403
    assert denied(client, "/v1/realtime/speech?voice=nobody", ALL).status_code == 400
    assert denied(client, "/v1/realtime/transcription?language=Klingon", ALL).status_code == 400
    assert denied(client, "/v1/realtime/transcription", {"authorization": "Bearer speech:create"}).status_code == 403
    service.warm = False
    r = denied(client, "/v1/realtime/speech?voice=ryan", ALL)
    assert r.status_code == 503 and r.headers["retry-after"] == "2"


# ---- TTS sessions ------------------------------------------------------------------------------------
def test_a_reply_streamed_in_comes_back_as_audio_then_done():
    client, service = make()
    with client.websocket_connect("/v1/realtime/speech?voice=ryan", headers=ALL) as ws:
        assert ws.receive_json() == {"type": "started", "format": "pcm_s16le", "sample_rate": 24000, "channels": 1}
        for piece in ["Sure, tomorrow ", "looks cloudy. And ", "then some rain"]:
            ws.send_json({"type": "text", "text": piece})
        ws.send_json({"type": "end"})
        msgs, audio, code = drain(ws, with_code=True)
    segments = ["Sure,", "tomorrow looks cloudy.", "And then some rain"]  # the first clause starts at once
    words = sum(len(t.split()) for t in segments)
    assert msgs == [{"type": "sentence", "index": i, "text": t} for i, t in enumerate(segments)] \
        + [{"type": "done", "samples": words * SPF}]                       # one generation: done once
    assert audio == words * SPF * 2                                       # 16-bit: 2 bytes a sample
    assert code == 1000
    assert service.tts_admission.counts()["running"] == 0
    assert len(service.first_audio_ms) == 1


def test_a_flush_speaks_the_passage_whole_and_the_reply_carries_on():
    """The agent's "let me check" before a search, then its answer: the unfinished sentence goes out at the
    flush (no sentence end yet), then the answer; done once, every word spoken."""
    client, service = make()
    with client.websocket_connect("/v1/realtime/speech?voice=ryan", headers=ALL) as ws:
        ws.receive_json()
        ws.send_json({"type": "text", "text": "Sure, let me check."})        # "." at the end: the splitter waits
        ws.send_json({"type": "flush"})
        ws.send_json({"type": "text", "text": "It is sunny today."})
        ws.send_json({"type": "end"})
        msgs, audio, code = drain(ws, with_code=True)
    segments = ["Sure,", "let me check.", "It is sunny today."]
    assert [m["text"] for m in msgs if m["type"] == "sentence"] == segments
    assert msgs[-1] == {"type": "done", "samples": 8 * SPF} and audio == 8 * SPF * 2 and code == 1000


def test_two_run_one_waits_then_starts_and_the_fourth_is_refused():
    client, service = make(slots=2)
    url = "/v1/realtime/speech?voice=ryan"
    with client.websocket_connect(url, headers=ALL) as a, client.websocket_connect(url, headers=ALL) as b:
        assert a.receive_json()["type"] == "started" and b.receive_json()["type"] == "started"
        with client.websocket_connect(url, headers=ALL) as c:
            assert c.receive_json() == {"type": "queued", "position": 1}
            r = denied(client, url, ALL)
            assert r.status_code == 503 and "retry-after" in r.headers
            a.send_json({"type": "end"})                                         # a's reply: nothing; done
            assert drain(a)[0] == [{"type": "done", "samples": 0}]
            assert c.receive_json()["type"] == "started"                         # c gets a's place


def test_waiting_too_long_gives_up():
    client, service = make(slots=1)
    url = "/v1/realtime/speech?voice=ryan"
    with client.websocket_connect(url, headers=ALL) as a:
        a.receive_json()
        a.send_json({"type": "text", "text": "word " * 300 + ". "})               # ~1.5 s of fake speech
        with client.websocket_connect(url, headers=ALL) as b:
            assert b.receive_json()["type"] == "queued"
            assert drain(b)[0][-1]["code"] == "queue_timeout"


def test_idle_and_too_much_text_end_the_session_and_free_the_place():
    client, service = make()
    with client.websocket_connect("/v1/realtime/speech?voice=ryan", headers=ALL) as ws:
        ws.receive_json()
        msgs, _, code = drain(ws, with_code=True)
        assert msgs[-1]["code"] == "idle" and code == 1008
    client2, service2 = make(MAX_TEXT_CHARS=10)
    with client2.websocket_connect("/v1/realtime/speech?voice=ryan", headers=ALL) as ws:
        ws.receive_json()
        ws.send_json({"type": "text", "text": "far more than ten characters"})
        assert drain(ws)[0][-1]["code"] == "too_long"
    assert service.tts_admission.counts()["running"] == 0 and service2.tts_admission.counts()["running"] == 0


def test_cancel_and_disconnect_free_the_place():
    client, service = make(slots=1)
    url = "/v1/realtime/speech?voice=ryan"
    with client.websocket_connect(url, headers=ALL) as ws:
        ws.receive_json()
        ws.send_json({"type": "text", "text": "a long sentence that is still being spoken. "})
        ws.send_json({"type": "cancel"})
        msgs, _, code = drain(ws, with_code=True)
        assert msgs[-1] == {"type": "cancelled"} and code == 1000
    with client.websocket_connect(url, headers=ALL) as ws:                       # disconnect mid-reply
        assert ws.receive_json()["type"] == "started"
        ws.send_json({"type": "text", "text": "another one. "})
    time.sleep(0.2)
    assert service.tts_admission.counts()["running"] == 0


def test_a_bad_message_ends_the_session():
    client, _ = make()
    with client.websocket_connect("/v1/realtime/speech?voice=ryan", headers=ALL) as ws:
        ws.receive_json()
        ws.send_text("not json")
        assert drain(ws)[0][-1]["code"] == "bad_message"


# ---- STT sessions ------------------------------------------------------------------------------------
def test_an_utterance_gives_partials_then_the_final():
    client, service = make()
    with client.websocket_connect("/v1/realtime/transcription", headers=ALL) as ws:
        assert ws.receive_json() == {"type": "started", "format": "pcm_s16le", "sample_rate": 16000, "channels": 1}
        for _ in range(35):                                                     # 3.5 s, 100 ms at a time
            ws.send_bytes(np.zeros(1600, dtype="<i2").tobytes())
            time.sleep(0.01)
        time.sleep(0.2)
        ws.send_json({"type": "commit"})
        msgs, _ = drain(ws)
    partials = [m for m in msgs if m["type"] == "partial"]
    assert msgs[-1] == {"type": "final", "text": "hello world 3s", "language": "English", "reason": "commit"}
    assert partials and all(set(p) == {"type", "text", "stable", "language"} for p in partials)
    assert any(p["stable"] and p["text"].startswith(p["stable"]) for p in partials)   # after 2 passes
    assert service.stt_admission.counts()["running"] == 0 and len(service.final_ms) == 1


def test_an_utterance_with_no_audio_goes_idle():
    client, _ = make()
    with client.websocket_connect("/v1/realtime/transcription", headers=ALL) as ws:
        ws.receive_json()
        msgs, _, code = drain(ws, with_code=True)
        assert msgs[-1]["code"] == "idle" and code == 1008


def test_audio_then_silence_without_a_commit_is_still_heard():
    """The client stops sending without a commit: after the idle time, what was heard is the final."""
    client, _ = make()
    with client.websocket_connect("/v1/realtime/transcription", headers=ALL) as ws:
        ws.receive_json()
        for _ in range(12):
            ws.send_bytes(np.zeros(1600, dtype="<i2").tobytes())
        msgs, _, code = drain(ws, with_code=True)
    assert msgs[-1] == {"type": "final", "text": "hello world 1s", "language": "English", "reason": "idle"}
    assert code == 1000


def test_cancel_is_acknowledged():
    client, _ = make()
    with client.websocket_connect("/v1/realtime/transcription", headers=ALL) as ws:
        ws.receive_json()
        ws.send_json({"type": "cancel"})
        msgs, _, code = drain(ws, with_code=True)
    assert msgs[-1] == {"type": "cancelled"} and code == 1000


def test_odd_audio_is_a_bad_message():
    client, _ = make()
    with client.websocket_connect("/v1/realtime/transcription", headers=ALL) as ws:
        ws.receive_json()
        ws.send_bytes(b"\x00\x01\x02")
        msgs, _, code = drain(ws, with_code=True)
        assert msgs[-1]["code"] == "bad_message" and code == 1008


# ---- HTTP ----------------------------------------------------------------------------------------------
def test_one_shot_speech_as_pcm_and_wav():
    client, _ = make()
    text = "Sure. Tomorrow looks cloudy."
    r = client.post("/v1/audio/speech", json={"input": text, "voice": "ryan"}, headers=ALL)
    assert r.status_code == 200 and r.headers["x-sample-rate"] == "24000"
    assert len(r.content) == len(text.split()) * SPF * 2
    w = client.post("/v1/audio/speech", json={"input": text, "voice": "ryan", "response_format": "wav"}, headers=ALL)
    assert w.content[:4] == b"RIFF" and len(w.content) == 44 + len(r.content)
    assert client.post("/v1/audio/speech", json={"input": "hi", "voice": "nobody"}, headers=ALL).status_code == 400
    assert client.post("/v1/audio/speech", json={"input": "hi", "voice": "ryan"}).status_code == 401


def test_voices_status_health():
    client, service = make()
    assert client.get("/v1/voices", headers=ALL).json()["voices"][0] == {"name": "ryan", "language": "English"}
    st = client.get("/v1/status", headers=ALL).json()
    assert st["warm"] and st["tts"]["running_max"] == 2 and st["stt"]["waiting_max"] == 1
    assert client.get("/v1/status", headers={"authorization": "Bearer speech:create"}).status_code == 403
    assert client.get("/health").status_code == 200
    service.warm = False
    assert client.get("/health").status_code == 503



def test_server_side_turn_detection_commits_by_itself():
    from tests.test_turns import loud, quiet
    client, _ = make()
    with client.websocket_connect("/v1/realtime/transcription?turn_detection=server&silence_ms=500", headers=ALL) as ws:
        ws.receive_json()
        audio = np.concatenate([quiet(0.3), loud(1.2), quiet(1.0)])
        for i in range(0, len(audio), 1600):
            ws.send_bytes((audio[i:i + 1600] * 32767).astype("<i2").tobytes())
        msgs, _, code = drain(ws, with_code=True)                      # no commit sent
    assert msgs[-1]["type"] == "final" and msgs[-1]["reason"] == "silence" and code == 1000
    assert denied(client, "/v1/realtime/transcription?turn_detection=maybe", ALL).status_code == 400
    assert denied(client, "/v1/realtime/transcription?turn_detection=server&silence_ms=50", ALL).status_code == 400


def test_a_transcription_carries_its_context_after_the_services_own():
    """T18: the service's context (none by default), then the session's, in every pass's prompt."""
    def prompts_for(client, service, context):
        with client.websocket_connect("/v1/realtime/transcription?context=" + context.replace(" ", "%20"), headers=ALL) as ws:
            ws.receive_json()
            ws.send_bytes(np.zeros(16000, dtype="<i2").tobytes())
            ws.send_json({"type": "commit"})
            msgs, _ = drain(ws)
        assert msgs[-1]["type"] == "final"
        return [p for run in service.stt.batch.engine.passes for _, p, _ in run]

    client, service = make()                                          # no service context: the session's only
    prompts = prompts_for(client, service, "Steven lives in Seattle.")
    assert prompts and all(p.endswith("[Steven lives in Seattle.]") for p in prompts)
    client, service = make(STT_CONTEXT="A lab hint.")                 # set: it comes first
    prompts = prompts_for(client, service, "Steven lives in Seattle.")
    assert all("[A lab hint.\nSteven lives in Seattle.]" in p for p in prompts)
    too_long = "/v1/realtime/transcription?context=" + "x" * 1001
    assert denied(client, too_long, ALL).status_code == 400
