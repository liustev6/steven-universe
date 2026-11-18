"""Streaming speech-to-text, the logic (speech-service.md T12, 4.5d; spike 10). Pure code: the model's
one decode pass is passed in, so this is tested without a GPU.

Qwen3-ASR streams by re-deciding, not by appending (its own `streaming_transcribe`):

    every pass:  ALL the audio so far  +  prompt + prefix  →  the model writes the rest of the text
    prefix:      the text so far, less its last 5 tokens (the tail may still change as more audio
                 arrives); no prefix for the first 2 passes (too little audio to trust any text)

So text that has settled stays put, and only the tail is re-decided. A pass re-reads all the audio, so
its cost grows with the utterance (spike 10: 179 ms by ~15 s); our schedule spaces the passes out as
it grows, keeping the cost per second flat (spike 10's risk 1, option a):

    heard   0-10 s   a pass every 1 s     (partial text every second: the usual voice request)
           10-20 s   every 2 s
            20 s +   every 4 s            up to the 60 s limit (T13)

If audio arrives faster than passes (a burst, or the GPU busy), one pass covers it all: passes never
queue up behind each other.

A session decodes by itself (`feed`, `finish`), or a batch drives it (batch.py: `add` audio, ask
whether it's `due`, take its `pending` pass, `apply` the result), so several sessions share a pass.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np

from app.stt_engine.base import TooLong, Transcript

SAMPLE_RATE = 16000


def pass_interval(seconds_heard: float) -> float:
    """Seconds of new audio before the next pass, given how much has been heard."""
    if seconds_heard < 10:
        return 1.0
    if seconds_heard < 20:
        return 2.0
    return 4.0


def settled_prefix(raw: str, passes: int, tokenizer, *, unfixed_passes: int = 2, unfixed_tokens: int = 5) -> str:
    """Qwen's rollback: the text so far less its last `unfixed_tokens` tokens (more, if the cut would
    leave a broken character, U+FFFD); nothing for the first `unfixed_passes` passes."""
    if passes < unfixed_passes:
        return ""
    ids, k = tokenizer.encode(raw), unfixed_tokens
    while True:
        end = max(0, len(ids) - k)
        prefix = tokenizer.decode(ids[:end]) if end else ""
        if "�" not in prefix:
            return prefix
        k += 1


class StreamingTranscription:
    """One utterance. `decode(audio, prefix)` → the raw text (prefix + what the model wrote);
    `parse(raw)` → (language, text): Qwen's output carries its detected language as a tag."""

    def __init__(self, decode: Callable[[np.ndarray, str], str], tokenizer, parse: Callable[[str], tuple[str, str]],
                 *, max_seconds: float = 60.0):
        self._decode, self._tokenizer, self._parse = decode, tokenizer, parse
        self.max_samples = int(max_seconds * SAMPLE_RATE)
        self.audio = np.zeros(0, dtype=np.float32)
        self.raw, self.passes = "", 0
        self.decoded_upto = 0                         # samples covered by the last pass
        self.next_pass_at = int(pass_interval(0) * SAMPLE_RATE)   # samples at which the next pass is due

    # ---- deciding by itself ------------------------------------------------------------------------
    def feed(self, pcm: np.ndarray) -> Transcript | None:
        self.add(pcm)
        if not self.due():
            return None
        return self.apply(self._decode(*self.pending()), final=False)

    def finish(self) -> Transcript:
        """The final text: one more pass if audio arrived since the last (else the last pass's text);
        nothing at all if no audio ever came."""
        if len(self.audio) == 0:
            return Transcript("", "", final=True)
        if self.needs_final_pass():
            return self.apply(self._decode(*self.pending()), final=True)
        language, text = self._parse(self.raw)
        return Transcript(language, text, final=True)

    # ---- the steps a batch drives ------------------------------------------------------------------
    def add(self, pcm: np.ndarray) -> None:
        """More audio (16 kHz mono, float32 or int16); TooLong past the limit."""
        pcm = np.asarray(pcm).reshape(-1)
        pcm = pcm.astype(np.float32) / 32768.0 if pcm.dtype == np.int16 else pcm.astype(np.float32, copy=False)
        if len(self.audio) + len(pcm) > self.max_samples:
            raise TooLong(f"over {self.max_samples / SAMPLE_RATE:.0f} s")
        self.audio = np.concatenate([self.audio, pcm])

    def due(self) -> bool:
        """Enough new audio for the next pass (its interval has passed)."""
        return len(self.audio) >= self.next_pass_at

    def nearly_due(self, fraction: float = 0.5) -> bool:
        """At least `fraction` of the interval's audio is in: worth joining a pass that runs anyway."""
        interval = self.next_pass_at - self.decoded_upto
        return len(self.audio) - self.decoded_upto >= fraction * interval

    def needs_final_pass(self) -> bool:
        return len(self.audio) > 0 and (self.passes == 0 or len(self.audio) > self.decoded_upto)

    def pending(self) -> tuple[np.ndarray, str]:
        """The next pass's input: all the audio so far, and the settled text as its prefix."""
        return self.audio, settled_prefix(self.raw, self.passes, self._tokenizer)

    def apply(self, raw: str, *, final: bool) -> Transcript:
        """A pass's result (the raw text: prefix + what the model wrote); schedules the next pass."""
        self.raw = raw
        self.passes, self.decoded_upto = self.passes + 1, len(self.audio)
        self.next_pass_at = len(self.audio) + int(pass_interval(len(self.audio) / SAMPLE_RATE) * SAMPLE_RATE)
        language, text = self._parse(raw)
        return Transcript(language, text, final=final, stable=text if final else self.stable(text))

    def stable(self, text: str) -> str:
        """The part of `text` the next pass will keep: the settled prefix it'll be given, as text
        ("" for the first passes, or if the cut fell inside Qwen's language tag)."""
        prefix = settled_prefix(self.raw, self.passes, self._tokenizer)
        _, settled = self._parse(prefix) if prefix else ("", "")
        return settled if settled and text.startswith(settled) else ""
