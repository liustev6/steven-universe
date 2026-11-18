"""Server-side turn detection (app/stt_engine/turns.py) on synthetic audio: quiet noise and a tone."""

import numpy as np

from app.stt_engine.turns import SilenceEndpoint

SR = 16000
rng = np.random.default_rng(0)


def quiet(s):                       # ~-66 dBFS noise
    return rng.normal(0, 5e-4, int(s * SR)).astype(np.float32)


def loud(s):                        # ~-23 dBFS: speech-like level
    t = np.arange(int(s * SR)) / SR
    return (0.1 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


def feed(e, audio, piece=1600):
    """When the turn ended, in seconds into `audio` (None if it didn't)."""
    for i in range(0, len(audio), piece):
        if e.feed(audio[i:i + piece]):
            return (i + piece) / SR
    return None


def test_the_turn_ends_after_the_silence_that_follows_speech():
    audio = np.concatenate([quiet(0.5), loud(1.0), quiet(2.0)])
    ended = feed(SilenceEndpoint(silence_ms=700), audio)
    assert ended is not None and 1.5 + 0.7 <= ended <= 1.5 + 0.7 + 0.1   # 700 ms after speech, at 100 ms pieces


def test_quiet_alone_never_ends_a_turn():
    assert feed(SilenceEndpoint(), quiet(5.0)) is None


def test_a_click_isnt_speech():
    audio = np.concatenate([quiet(0.5), loud(0.06), quiet(2.0)])
    assert feed(SilenceEndpoint(), audio) is None


def test_a_pause_shorter_than_the_silence_doesnt_end_it():
    audio = np.concatenate([loud(1.0), quiet(0.4), loud(1.0), quiet(0.2)])
    assert feed(SilenceEndpoint(silence_ms=700), audio) is None
