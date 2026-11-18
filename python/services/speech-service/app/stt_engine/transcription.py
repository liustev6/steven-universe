"""Streaming speech-to-text, the logic (speech-service.md T12, 4.5d; spikes/10). Pure code: the model's
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
        self.next_pass_at = int(pass_interval(0) * SAMPLE_RATE)

    def feed(self, pcm: np.ndarray) -> Transcript | None:
        pcm = np.asarray(pcm).reshape(-1)
        pcm = pcm.astype(np.float32) / 32768.0 if pcm.dtype == np.int16 else pcm.astype(np.float32, copy=False)
        if len(self.audio) + len(pcm) > self.max_samples:
            raise TooLong(f"over {self.max_samples / SAMPLE_RATE:.0f} s")
        self.audio = np.concatenate([self.audio, pcm])
        if len(self.audio) < self.next_pass_at:
            return None
        transcript = self._pass(final=False)
        heard = len(self.audio) / SAMPLE_RATE
        self.next_pass_at = len(self.audio) + int(pass_interval(heard) * SAMPLE_RATE)
        return transcript

    def finish(self) -> Transcript:
        """The final text: one more pass if audio arrived since the last (else the last pass's text)."""
        if self.passes == 0 or len(self.audio) > self.decoded_upto:
            return self._pass(final=True)
        language, text = self._parse(self.raw)
        return Transcript(language, text, final=True)

    def _pass(self, *, final: bool) -> Transcript:
        prefix = settled_prefix(self.raw, self.passes, self._tokenizer)
        self.raw = self._decode(self.audio, prefix)
        self.passes, self.decoded_upto = self.passes + 1, len(self.audio)
        language, text = self._parse(self.raw)
        return Transcript(language, text, final=final)
