"""The speech-to-text engine on the real models and GPU (marked `gpu`: run on CT 108, skipped elsewhere):

    STT_MODEL=/models/Qwen3-ASR-0.6B TTS_MODEL=/models/Qwen3-TTS-12Hz-0.6B-CustomVoice python -m pytest -m gpu

No test data: the TTS says a sentence (greedy, so always the same audio), the STT hears it back."""

import os
import re
import time
from difflib import SequenceMatcher

import numpy as np
import pytest

torch = pytest.importorskip("torch")
pytestmark = [pytest.mark.gpu,
              pytest.mark.skipif(not (os.environ.get("STT_MODEL") and os.environ.get("TTS_MODEL")
                                      and torch.cuda.is_available()),
                                 reason="needs both models (STT_MODEL, TTS_MODEL) and a GPU")]

ENGLISH = ("ryan", "The kitchen lights are on, and the door is locked.")
CHINESE = ("vivian", "好的，我现在帮你把客厅的灯关掉。")
LONGER = [("aiden", "Sure. Tomorrow looks cloudy in the morning, with a little rain in the afternoon."),
          ("ryan", "So take an umbrella if you go out after lunch, and maybe a warm jacket too."),
          ("aiden", "On Saturday the sun comes back, and it should be the warmest day of the week.")]


@pytest.fixture(scope="module")
def tts():
    from app.tts_engine.engine import QwenTTSEngine

    e = QwenTTSEngine(os.environ["TTS_MODEL"], predictor="ours-fused", talker="ours-graph")
    e.load()
    e.warm_up()
    return e


@pytest.fixture(scope="module")
def stt():
    from app.stt_engine.engine import QwenSTTEngine

    e = QwenSTTEngine(os.environ["STT_MODEL"])
    e.load()
    e.warm_up()
    return e


def say(tts, voice, text):
    """The TTS's 24 kHz speech, as the Pi's 16 kHz would be."""
    from scipy.signal import resample_poly

    audio = np.concatenate(list(tts.stream(text, voice, sample=False)))
    return resample_poly(audio, 2, 3).astype(np.float32)


def same(a, b):
    """How alike two texts are, ignoring case, spaces and punctuation (1.0: identical)."""
    norm = lambda t: re.sub(r"[\W_]+", "", t.lower())  # noqa: E731
    return SequenceMatcher(None, norm(a), norm(b)).ratio()


@pytest.mark.parametrize("voice, text", [ENGLISH, CHINESE])
def test_whole_utterance_is_qwens_own_transcribe(tts, stt, voice, text):
    audio = say(tts, voice, text)
    ours = stt.transcribe(audio)
    qwens = stt._asr.transcribe(audio=(audio, 16000))[0]
    assert (ours.language, ours.text) == (qwens.language, qwens.text)


@pytest.mark.parametrize("voice, text", [ENGLISH, CHINESE])
def test_it_hears_what_the_tts_said(tts, stt, voice, text):
    heard = stt.transcribe(say(tts, voice, text))
    assert heard.final and same(heard.text, text) > 0.9, heard.text
    assert heard.language == ("English" if voice == "ryan" else "Chinese")


def test_streaming_partials_then_the_final_soon_after_the_end(tts, stt):
    audio = say(tts, *ENGLISH)
    s, partials = stt.session(), []
    for i in range(0, len(audio), 1600):                       # 100 ms at a time, as the Pi sends it
        if (t := s.feed(audio[i:i + 1600])) is not None:
            partials.append(t.text)
    start = time.perf_counter()
    final = s.finish()
    took = time.perf_counter() - start
    assert len(partials) >= len(audio) // 16000 - 1           # about one a second
    assert final.final and same(final.text, ENGLISH[1]) > 0.9, final.text
    assert took < 0.3, f"final took {took * 1000:.0f} ms"


def test_silence_says_nothing(stt):
    quiet = np.random.default_rng(1).normal(0, 1e-3, 2 * 16000).astype(np.float32)
    assert stt.transcribe(quiet).text.strip() == ""


def test_a_language_can_be_asked_for(tts, stt):
    heard = stt.transcribe(say(tts, *CHINESE), language="Chinese")
    assert heard.language == "Chinese" and same(heard.text, CHINESE[1]) > 0.9


def test_a_long_utterance_keeps_up(tts, stt):
    """~30 s of speech, streamed: every pass finishes well inside its interval (the schedule spaces
    them out as the audio grows), and the end is heard."""
    gap = np.zeros(8000, dtype=np.float32)
    audio = np.concatenate([part for voice, text in LONGER * 2 for part in (say(tts, voice, text), gap)])
    assert len(audio) > 25 * 16000
    s, worst = stt.session(), 0.0
    for i in range(0, len(audio), 1600):
        start = time.perf_counter()
        s.feed(audio[i:i + 1600])
        worst = max(worst, time.perf_counter() - start)
    final = s.finish()
    assert worst < 1.0, f"a pass took {worst * 1000:.0f} ms"
    assert "warmest day" in final.text.lower(), final.text


# ---- the batch (batch.py) --------------------------------------------------------------------------
def test_a_batched_pass_gives_each_row_its_own_text(tts, stt):
    """Two utterances of different lengths and languages in one padded pass: each row's text is the
    text it gets alone (the prompts are padded on the left, so each row continues its own)."""
    a, b = say(tts, *ENGLISH), say(tts, *CHINESE)
    prompt = stt.prompt()
    alone = [stt.decode_batch([x], [prompt], [""])[0] for x in (a, b)]
    together = stt.decode_batch([a, b], [prompt, prompt], ["", ""])
    assert together == alone


def stream_through(batch, clips, starts):
    """Each clip into its own slot, 100 ms at a time, clip i starting at `starts[i]` seconds; a tick
    after every 100 ms; each slot ended when its audio is done. The finals, in clip order."""
    slots, finals, step = {}, {}, 1600
    total = max(s * 16000 + len(c) for s, c in zip(starts, clips))
    for at in range(0, int(total) + step, step):
        for i, (s, clip) in enumerate(zip(starts, clips)):
            offset = at - int(s * 16000)
            if offset == 0:
                slots[i] = batch.join()
            if i in slots and 0 <= offset < len(clip):
                batch.add(slots[i], clip[offset:offset + step])
                if offset + step >= len(clip):
                    batch.end(slots[i])
        for slot, t in batch.tick().items():
            if t.final:
                finals[next(i for i, x in slots.items() if x == slot)] = t.text
                slots = {i: x for i, x in slots.items() if x != slot}
    return [finals[i] for i in range(len(clips))]


def test_two_streams_share_passes_and_are_heard_right(tts, stt):
    clips = [say(tts, *ENGLISH), say(tts, *CHINESE)]
    batch = stt.batch(slots=2)
    finals = stream_through(batch, clips, starts=[0, 0.6])
    assert same(finals[0], ENGLISH[1]) > 0.9 and same(finals[1], CHINESE[1]) > 0.9, finals
    separate = sum(len(c) // 16000 + 1 for c in clips)              # about one pass a second each
    assert batch.passes < separate


def test_four_streams(tts, stt):
    sentences = [ENGLISH, CHINESE, LONGER[0], LONGER[1]]
    clips = [say(tts, *s) for s in sentences]
    finals = stream_through(stt.batch(slots=4), clips, starts=[0, 0.3, 0.7, 1.2])
    for (voice, text), heard in zip(sentences, finals):
        assert same(heard, text) > 0.9, (text, heard)
