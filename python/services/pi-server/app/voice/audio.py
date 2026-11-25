"""The Pi's mic and speaker, through ALSA with `arecord` and `aplay` as child processes (voice-path.md
V2, the way Home Assistant's Wyoming satellite does it): raw 16-bit mono, the mic at 16 kHz (the
speakerphone's own format), the speaker at 24 kHz (speech-service's; ALSA's plughw converts it to the
device's 48 kHz stereo). pi-server is a system service with no desktop session, so no PipeWire.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import AsyncIterator

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


class Speaker:
    """One reply's playback: what's written plays in order; `drain` waits until it has all played,
    `stop` cuts it off at once (cancel, and later barge-in)."""

    def __init__(self, device: str):
        self.device = device
        self._proc: asyncio.subprocess.Process | None = None

    async def open(self) -> None:
        self._proc = await asyncio.create_subprocess_exec(
            "aplay", "-q", "-D", self.device, "-r", str(SPEAKER_RATE), "-c", "1", "-f", "S16_LE", "-t", "raw",
            stdin=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)

    async def write(self, pcm: bytes) -> None:
        if self._proc is None:
            await self.open()
        try:
            self._proc.stdin.write(pcm)
            await self._proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as e:
            err = (await self._proc.stderr.read()).decode(errors="replace").strip()
            raise AudioError(f"the speaker stopped: {err or e}") from e

    async def drain(self) -> None:
        if self._proc is None:
            return
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            self._proc.stdin.close()
            await self._proc.stdin.wait_closed()
        await self._proc.wait()
        self._proc = None

    async def stop(self) -> None:
        if self._proc is not None:
            proc, self._proc = self._proc, None
            await _stop(proc)
            # Its stdin closed and awaited: aplay gone mid-write leaves the pipe's BrokenPipeError on asyncio's
            # close future, logged later as "Future exception was never retrieved" if nobody reads it.
            with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                proc.stdin.close()
                await proc.stdin.wait_closed()
