"""The plain engine on the real model and GPU (marked `gpu`: run on CT 108, skipped elsewhere):

    TTS_MODEL=/models/Qwen3-TTS-12Hz-0.6B-CustomVoice python -m pytest -m gpu

The key test: without sampling, our streaming engine produces exactly Qwen's own codes, frame for
frame. Every faster engine (4.5c) must pass the same test."""

import os
import threading
import time

import numpy as np
import pytest

torch = pytest.importorskip("torch")
pytestmark = [pytest.mark.gpu,
              pytest.mark.skipif(not (os.environ.get("TTS_MODEL") and torch.cuda.is_available()),
                                 reason="needs the model (TTS_MODEL) and a GPU")]

SENTENCES = [("ryan", "Hi, how can I help you today?"),
             ("vivian", "好的，我现在帮你把客厅的灯关掉。"),
             ("ryan", "Sure. Tomorrow looks cloudy in the morning, with a little rain in the afternoon.")]


@pytest.fixture(scope="module")
def engine():
    from app.engine.qwen import QwenEngine

    e = QwenEngine(os.environ["TTS_MODEL"])
    e.load()
    e.warm_up()
    return e


@pytest.mark.parametrize("voice, text", SENTENCES)
def test_streamed_codes_are_qwens_own(engine, voice, text):
    ours = torch.stack(list(engine.frames(text, voice, sample=False)))
    official = engine.official_codes(text, voice)
    assert ours.shape == official.shape, (ours.shape, official.shape)
    assert torch.equal(ours.cpu(), official.cpu())


@pytest.mark.parametrize("voice, text", SENTENCES)
def test_streamed_audio_matches_the_whole_decode(engine, voice, text):
    """Chunks decoded with 25 frames of context sound like the whole sentence decoded at once."""
    codes = engine.official_codes(text, voice)
    whole, sr = engine._codec.decode([{"audio_codes": codes}])
    assert sr == engine.sample_rate == 24000
    streamed = np.concatenate(list(engine.stream(text, voice, sample=False)))
    whole = np.asarray(whole[0], dtype=np.float32)
    assert abs(len(streamed) - len(whole)) <= 1920                # within one frame
    n = min(len(streamed), len(whole))
    noise = np.sum((streamed[:n] - whole[:n]) ** 2)
    snr_db = 10 * np.log10(np.sum(whole[:n] ** 2) / max(noise, 1e-12))
    assert snr_db > 20, f"streamed vs whole: {snr_db:.1f} dB"


def test_voices(engine):
    voices = {v.name: v.language for v in engine.voices()}
    assert len(voices) == 9 and voices["vivian"] == "Chinese" and voices["ryan"] == "English"


def test_cancel_stops_within_a_frame_or_two(engine):
    cancel = threading.Event()
    chunks = 0
    with pytest.raises(Exception) as exc:
        for _ in engine.stream("Count slowly from one to fifty, please, one number at a time.", "ryan", cancel=cancel):
            chunks += 1
            if chunks == 2:
                t = time.perf_counter()
                cancel.set()
    assert type(exc.value).__name__ == "Cancelled"
    assert time.perf_counter() - t < 0.5
    list(engine.stream("Still fine afterwards.", "ryan"))       # the engine is usable again


def test_speed(engine):
    """The spike's numbers, as a floor: first audio under 300 ms, faster than real time."""
    t = time.perf_counter()
    first, samples = None, 0
    for audio in engine.stream("The kitchen lights are on, and the door is locked.", "aiden"):
        first = first or (time.perf_counter() - t)
        samples += len(audio)
    total = time.perf_counter() - t
    assert first < 0.3, f"first audio {first * 1000:.0f} ms"
    assert total / (samples / 24000) < 0.7, f"real-time factor {total / (samples / 24000):.2f}"
