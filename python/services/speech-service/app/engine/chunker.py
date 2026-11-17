"""Streaming decode: codec frames in, new audio out (speech-service.md T11).

The model makes one codec frame at a time (12.5 a second; each frame = 1,920 samples at 24 kHz). The
codec's decoder turns frames into audio, but a frame's sound depends on the frames before it. So each
time a chunk of new frames has arrived, the decoder runs on them **plus up to `context` earlier
frames**, and only the audio of the new frames is kept:

    frames:   0 1 2 … 25 26 27 28 29 30 31 32
    chunk 4:  [── context: 4…28 ──][29 30 31 32]  → decode 4…32, keep only 29…32's samples

**Chunk sizes follow a schedule**, the last size repeating: the default (1, 1, 2, 4) decodes after frame
1, then 1 and 2 more, then every 4. The first audio waits for one frame only (~30 ms a frame saved; the
chunk spike in speech-service.md). The second chunk is the tightest moment: the player has only frame 1's
80 ms queued, so it must be small (1, 2, 4 left 12 ms of margin; 1, 1, … leaves 43). Then the sizes grow
as the queue does, and the larger chunks keep the decode work low (each decode re-reads up to `context`
frames). The sound is the same either way: the codec is
causal (a frame's audio depends only on earlier frames), so cutting right after the newest frame loses
nothing.

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
    def __init__(self, decode: Callable[[Sequence[Any]], np.ndarray], *, chunks: int | Sequence[int] = (1, 1, 2, 4),
                 context: int = 25, samples_per_frame: int = SAMPLES_PER_FRAME):
        self.chunks = (chunks,) if isinstance(chunks, int) else tuple(chunks)
        if not self.chunks or min(self.chunks) < 1 or context < 0:
            raise ValueError("chunk sizes must be at least 1, context at least 0")
        self._decode = decode
        self.context, self.spf = context, samples_per_frame
        self.frames: list[Any] = []
        self.emitted = 0                     # frames whose audio has been sent
        self.sent = 0                        # chunks sent: picks the next size

    @property
    def next_size(self) -> int:
        return self.chunks[min(self.sent, len(self.chunks) - 1)]

    def add(self, frame: Any) -> np.ndarray | None:
        """One more frame; the new audio once a chunk is complete, else None."""
        self.frames.append(frame)
        if len(self.frames) - self.emitted >= self.next_size:
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
        self.sent += 1
        return wav[begin:end]
