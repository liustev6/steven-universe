"""Streaming decode: codec frames in, new audio out (tts-service.md T11).

The model makes one codec frame at a time (12.5 a second; each frame = 1,920 samples at 24 kHz). The
codec's decoder turns frames into audio, but a frame's sound depends on the frames before it. So each
time `chunk` new frames have arrived, the decoder runs on them **plus up to `context` earlier frames**,
and only the audio of the new frames is kept:

    frames:   0 1 2 … 25 26 27 28 29 30 31 32
    chunk 8:  [── context: 4…27 ──][29 30 31 32]  → decode 4…32, keep only 29…32's samples

Mid-sentence, a decode's samples past its last frame (codec padding) are dropped: the next chunk
produces them again, properly. Only `flush()`, the sentence's end, keeps them. Pure code: the decoder
is passed in, so this is tested without a GPU.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

import numpy as np

SAMPLES_PER_FRAME = 1920      # 24,000 Hz ÷ 12.5 frames a second (the 12 Hz codec's decode_upsample_rate)


class ChunkDecoder:
    def __init__(self, decode: Callable[[Sequence[Any]], np.ndarray], *, chunk: int = 4, context: int = 25,
                 samples_per_frame: int = SAMPLES_PER_FRAME):
        if chunk < 1 or context < 0:
            raise ValueError("chunk must be at least 1, context at least 0")
        self._decode = decode
        self.chunk, self.context, self.spf = chunk, context, samples_per_frame
        self.frames: list[Any] = []
        self.emitted = 0                     # frames whose audio has been sent

    def add(self, frame: Any) -> np.ndarray | None:
        """One more frame; the new audio once a chunk is complete, else None."""
        self.frames.append(frame)
        if len(self.frames) - self.emitted >= self.chunk:
            return self._emit(final=False)
        return None

    def flush(self) -> np.ndarray | None:
        """The end of the sentence: whatever hasn't been sent yet, with the decoder's tail. If the
        last chunk already went out mid-sentence (its tail cut), only the tail is left to send."""
        if len(self.frames) > self.emitted:
            return self._emit(final=True)
        if not self.frames:
            return None
        start = max(0, len(self.frames) - max(1, self.context))
        wav = np.asarray(self._decode(self.frames[start:]), dtype=np.float32).reshape(-1)
        tail = wav[(len(self.frames) - start) * self.spf:]
        return tail if tail.size else None

    def _emit(self, final: bool) -> np.ndarray:
        start = max(0, self.emitted - self.context)
        wav = np.asarray(self._decode(self.frames[start:]), dtype=np.float32).reshape(-1)
        begin = (self.emitted - start) * self.spf
        end = None if final else (len(self.frames) - start) * self.spf
        self.emitted = len(self.frames)
        return wav[begin:end]
