"""The speaker, with `cat` into a file standing in for aplay: it's fed in real time, the reply's audio as
it comes and silence when there's none, so the audio before a pause (a web search mid-reply) goes out
before the pause, not after it."""

import asyncio
import time

import pytest

from app.voice.audio import AUDIO_LEAD, CHUNK_MS, SILENCE_LEAD, SPEAKER_RATE, AudioError, Speaker

BYTES_PER_S = SPEAKER_RATE * 2
TONE = b"\x01\x02"                      # a sample that isn't silence


def audio(seconds: float) -> bytes:
    return TONE * int(SPEAKER_RATE * seconds)


def speaker(tmp_path) -> tuple[Speaker, object]:
    out = tmp_path / "played.raw"
    return Speaker("unused", command=["sh", "-c", f"exec cat > {out}"]), out


def run(coro):
    asyncio.run(coro)


def test_the_audio_before_a_pause_goes_out_before_it_and_silence_fills_the_pause(tmp_path):
    async def go():
        s, out = speaker(tmp_path)
        await s.open()
        await s.write(audio(0.2))                          # "Sure, let me check." ...
        await asyncio.sleep(0.8)                           # ... the search
        played = out.read_bytes()
        first = played.index(TONE)
        assert played[first:first + len(audio(0.2))] == audio(0.2)            # all of it, in one piece
        after = played[first + len(audio(0.2)):]
        assert after and not after.strip(b"\0")                                # then silence
        await s.stop()

    run(go())


def test_it_stays_in_real_time_not_ahead(tmp_path):
    async def go():
        s, out = speaker(tmp_path)
        started = time.monotonic()
        await s.open()
        await s.write(audio(2.0))
        await asyncio.sleep(0.5)
        written = out.stat().st_size / BYTES_PER_S
        elapsed = time.monotonic() - started
        assert elapsed <= written <= elapsed + AUDIO_LEAD + 0.05
        await s.stop()

    run(go())


def test_audio_that_comes_during_silence_waits_behind_little_of_it(tmp_path):
    async def go():
        s, out = speaker(tmp_path)
        started = time.monotonic()
        await s.open()
        await asyncio.sleep(0.5)                           # the agent thinking: silence
        at = time.monotonic() - started
        await s.write(audio(0.1))
        await asyncio.sleep(0.2)
        waited = out.read_bytes().index(TONE) / BYTES_PER_S - at
        assert waited <= SILENCE_LEAD + CHUNK_MS / 1000 + 0.02
        await s.stop()

    run(go())


def test_drain_plays_it_all_in_real_time_then_ends(tmp_path):
    async def go():
        s, out = speaker(tmp_path)
        await s.open()
        await s.write(audio(1.0) + b"\x07")                # an odd byte at the end: dropped, never stuck
        started = time.monotonic()
        await s.drain()
        took = time.monotonic() - started
        assert out.read_bytes().rstrip(b"\0").endswith(audio(1.0))
        assert 1.0 - AUDIO_LEAD - 0.1 <= took <= 1.2

    run(go())


def test_stop_cuts_it_off_at_once(tmp_path):
    async def go():
        s, out = speaker(tmp_path)
        await s.open()
        await s.write(audio(3.0))
        await asyncio.sleep(0.1)
        started = time.monotonic()
        await s.stop()
        assert time.monotonic() - started < 0.5
        assert out.stat().st_size < len(audio(1.0))

    run(go())


def test_a_speaker_that_dies_is_an_error(tmp_path):
    async def go():
        s = Speaker("unused", command=["sh", "-c", "echo 'no such device' >&2; exit 1"])
        await s.open()
        await asyncio.sleep(0.3)                           # its silence finds the pipe closed
        with pytest.raises(AudioError, match="no such device"):
            await s.write(audio(0.1))
        await s.stop()

    run(go())


def test_it_knows_when_what_it_was_given_has_played(tmp_path):
    """audio_ends: about now plus the audio not yet played; once past it, only silence plays."""
    async def go():
        s, _ = speaker(tmp_path)
        await s.open()
        t0 = time.monotonic()
        await s.write(audio(0.5))
        assert abs(s.audio_ends() - (t0 + 0.5)) < 0.1
        await asyncio.sleep(0.8)
        quiet_for = time.monotonic() - s.audio_ends()                 # played: silence since, ~0.3 s
        assert 0.2 < quiet_for < 0.4
        await s.stop()

    run(go())
