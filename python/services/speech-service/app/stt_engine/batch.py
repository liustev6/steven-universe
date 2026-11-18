"""The STT's batch: several utterances sharing passes (speech-service.md T13, 4.5e step 3; spikes/11:
a pass over 2 utterances costs ×1.64 one, over 4 ×2.24).

Each slot is one utterance in progress (a `StreamingTranscription`, transcription.py: its audio, its
text so far, its own schedule). Utterances arrive at unrelated moments, so passes are lined up on a
**tick** (the work loop calls `tick()` often, e.g. every 100 ms or whenever audio arrives):

    a pass runs     when any utterance is due (its interval's audio is in) or ending (its final)
    it includes     every utterance due or ending, and every one nearly due (half its interval's audio
                    in): pulled forward, so passes line up instead of each running alone
    finals          never wait for anything but the next tick; the slot is free once its final is out

One pass = one padded batch through the model (the processor pads the prompts on the left, so every
row's text continues its own prompt). Each row keeps its own prompt (a language, if asked for) and
its own prefix (its settled text).

    join(language)   a free slot for a new utterance (Full if none)
    add(slot, pcm)   its audio as it arrives (TooLong past the limit)
    end(slot)        the speaker stopped: its final comes at the next tick
    leave(slot)      cancelled: free at once
    tick()           {slot: Transcript} for every row the pass ran (partials, and finals)
"""

from __future__ import annotations

from collections.abc import Callable

from app.stt_engine.base import Transcript
from app.stt_engine.transcription import StreamingTranscription


class Full(Exception):
    """Every slot is busy."""


class STTBatch:
    def __init__(self, engine, *, slots: int = 2, nearly: float = 0.5,
                 decode_batch: Callable[[list, list[str]], list[str]] | None = None):
        """`engine`: a loaded QwenSTTEngine (its prompts, tokenizer, parser, and its batched pass).
        `nearly`: how much of its interval's audio an utterance needs to be pulled into a pass."""
        self.engine, self.slots, self.nearly = engine, slots, nearly
        self._decode_batch = decode_batch or engine.decode_batch
        self.sessions: list[StreamingTranscription | None] = [None] * slots
        self.prompts: list[str] = [""] * slots
        self.ending: set[int] = set()
        self.passes = 0                                   # batched passes run (for the spike's counts)

    def free(self) -> list[int]:
        return [i for i, s in enumerate(self.sessions) if s is None]

    def join(self, language: str | None = None) -> int:
        free = self.free()
        if not free:
            raise Full(f"all {self.slots} slots are busy")
        b = free[0]
        self.sessions[b] = self.engine.session(language, decode=None)
        self.prompts[b] = self.engine.prompt(language)
        return b

    def add(self, b: int, pcm) -> None:
        self.sessions[b].add(pcm)

    def end(self, b: int) -> None:
        self.ending.add(b)

    def leave(self, b: int) -> None:
        self.sessions[b] = None
        self.ending.discard(b)

    def tick(self) -> dict[int, Transcript]:
        busy = [b for b, s in enumerate(self.sessions) if s is not None]
        ending = [b for b in busy if b in self.ending]
        due = [b for b in busy if b not in self.ending and self.sessions[b].due()]
        if not ending and not due:
            return {}
        nearly = [b for b in busy if b not in self.ending and b not in due and self.sessions[b].nearly_due(self.nearly)]
        finals_done = {b: self._final_without_pass(b) for b in ending if not self.sessions[b].needs_final_pass()}
        rows = [b for b in ending if b not in finals_done] + due + nearly
        out: dict[int, Transcript] = dict(finals_done)
        if rows:
            pending = [self.sessions[b].pending() for b in rows]
            raws = self._decode_batch([audio for audio, _ in pending],
                                      [self.prompts[b] + prefix for b, (_, prefix) in zip(rows, pending)],
                                      [prefix for _, prefix in pending])
            self.passes += 1
            for b, raw in zip(rows, raws):
                out[b] = self.sessions[b].apply(raw, final=b in self.ending)
        for b in ending:
            self.sessions[b] = None
            self.ending.discard(b)
        return out

    def _final_without_pass(self, b: int) -> Transcript:
        return self.sessions[b].finish()            # no audio since the last pass, or none at all
