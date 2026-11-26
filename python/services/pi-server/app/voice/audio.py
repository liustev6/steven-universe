"""The Pi's mic and speaker, through ALSA with `arecord` and `aplay` as child processes (voice-path.md
V2, the way Home Assistant's Wyoming satellite does it): raw 16-bit mono, the mic at 16 kHz (the
speakerphone's own format), the speaker at 24 kHz (speech-service's; ALSA's plughw converts it to the
device's 48 kHz stereo). pi-server is a system service with no desktop session, so no PipeWire.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import AsyncIterator, Callable

logger = logging.getLogger(__name__)

MIC_RATE = 16_000
SPEAKER_RATE = 24_000
FRAME_BYTES = MIC_RATE * 2 // 10            # 100 ms of the mic


class AudioError(Exception):
    pass


async def _stop(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), 2)
        except asyncio.TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()


class Mic:
    """The mic as 100 ms frames, for as long as the caller reads them."""

    def __init__(self, device: str):
        self.device = device

    async def frames(self) -> AsyncIterator[bytes]:
        proc = await asyncio.create_subprocess_exec(
            "arecord", "-q", "-D", self.device, "-r", str(MIC_RATE), "-c", "1", "-f", "S16_LE", "-t", "raw",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            while True:
                try:
                    yield await proc.stdout.readexactly(FRAME_BYTES)
                except asyncio.IncompleteReadError:          # arecord ended on its own: the device failed
                    err = (await proc.stderr.read()).decode(errors="replace").strip()
                    raise AudioError(f"the mic stopped: {err or proc.returncode}")
        finally:
            await _stop(proc)


CHUNK_MS = 20                               # the speaker is fed this much at a time
SILENCE_LEAD = 0.06                         # silence: kept this far ahead of what's playing, no further
AUDIO_LEAD = 0.3                            # the reply's audio: written up to this far ahead
APLAY_PERIOD_US = 20_000                    # aplay's period: what it reads from stdin at a time
APLAY_START_US = 40_000                     # ... and plays once this much is in (its default: a full 500 ms)


def aplay_command(device: str) -> list[str]:
    return ["aplay", "-q", "-D", device, "-r", str(SPEAKER_RATE), "-c", "1", "-f", "S16_LE", "-t", "raw",
            f"--period-time={APLAY_PERIOD_US}", f"--start-delay={APLAY_START_US}"]


class Speaker:
    """One reply's playback, fed in real time from `open` to `drain`: the reply's audio as it comes,
    and silence whenever there's none (the agent thinking or searching), the way a sound card's
    callback or WebRTC's playout plays silence on an empty buffer. `write` queues audio; `drain`
    waits until it has all played; `stop` cuts it off at once (cancel, barge-in).

    Why it's fed continuously: aplay reads its stdin a period at a time and plays only once its start
    threshold is in (by default a full 500 ms buffer). Fed only the reply's audio, a pause (a web search
    between "let me check" and the answer) left the last partial period, or a whole short message, held
    in aplay until the next audio came, and it was heard then, at the next message's start (measured on
    the Pi: a 1.3 s message held 50 ms back, a 0.4 s one never started). Silence completes the period and
    keeps the stream running, so the audio before a pause is heard before it."""

    def __init__(self, device: str, command: list[str] | None = None,
                 clock: Callable[[], float] = time.monotonic):
        self.command = command or aplay_command(device)
        self.clock = clock
        self._proc: asyncio.subprocess.Process | None = None
        self._pump: asyncio.Task | None = None
        self._pending = bytearray()           # the reply's audio not yet written to aplay
        self._draining = False
        self._error: AudioError | None = None

    async def open(self) -> None:
        self._proc = await asyncio.create_subprocess_exec(
            *self.command, stdin=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        self._pending.clear()
        self._draining, self._error = False, None
        self._pump = asyncio.create_task(self._feed(self._proc))

    async def write(self, pcm: bytes) -> None:
        if self._proc is None:
            await self.open()
        self._raise()
        self._pending += pcm

    async def drain(self) -> None:
        if self._proc is None:
            return
        self._draining = True
        proc, pump = self._proc, self._pump
        await asyncio.wait([pump])
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            proc.stdin.close()
            await proc.stdin.wait_closed()
        await proc.wait()
        self._proc = self._pump = None
        self._raise()

    async def stop(self) -> None:
        if self._proc is not None:
            proc, pump, self._proc, self._pump = self._proc, self._pump, None, None
            pump.cancel()
            await asyncio.wait([pump])
            self._pending.clear()
            await _stop(proc)
            # Its stdin closed and awaited: aplay gone mid-write leaves the pipe's BrokenPipeError on asyncio's
            # close future, logged later as "Future exception was never retrieved" if nobody reads it.
            with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                proc.stdin.close()
                await proc.stdin.wait_closed()

    def _raise(self) -> None:
        if self._error is not None:
            raise self._error

    async def _feed(self, proc: asyncio.subprocess.Process) -> None:
        """Write to aplay on a clock: the reply's audio up to AUDIO_LEAD ahead of what's playing, silence
        up to SILENCE_LEAD when there's none (so audio that comes during a pause waits behind at most
        that much). Ends when draining and all the audio is written."""
        chunk = SPEAKER_RATE * 2 * CHUNK_MS // 1000
        silence = bytes(chunk)
        start, written = self.clock(), 0.0              # seconds of sound written since `start`
        try:
            while True:
                ahead = written - (self.clock() - start)
                if ahead < 0:                            # fell behind (the loop was held up): from now
                    start, written, ahead = self.clock(), 0.0, 0.0
                audio = len(self._pending) >= 2                      # a whole sample, at least
                if audio and ahead < AUDIO_LEAD:
                    n = min(len(self._pending), chunk) & ~1
                    data = bytes(self._pending[:n])
                    del self._pending[:n]
                elif audio:
                    await asyncio.sleep(ahead - AUDIO_LEAD + 0.001)
                    continue
                elif self._draining:
                    return
                elif ahead < SILENCE_LEAD:
                    data = silence
                else:
                    await asyncio.sleep(min(ahead - SILENCE_LEAD + 0.001, CHUNK_MS / 1000))
                    continue
                proc.stdin.write(data)
                await proc.stdin.drain()
                written += len(data) / (SPEAKER_RATE * 2)
        except (BrokenPipeError, ConnectionResetError) as e:
            err = (await proc.stderr.read()).decode(errors="replace").strip()
            self._error = AudioError(f"the speaker stopped: {err or e}")
