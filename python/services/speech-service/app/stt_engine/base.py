"""The STT engine's interface: what the service asks of a speech-to-text model (speech-service.md T12).
The TTS's is app/tts_engine/base.py.

    load(), warm_up()   the weights onto the GPU once; one throwaway pass before the service is healthy
    languages()         the languages it recognizes (it detects which, unless told)
    session(language)   one utterance, streamed in: `feed(pcm)` 16 kHz audio as it arrives (a partial
                        `Transcript` whenever a pass ran), `finish()` at the end of speech (the final)
    transcribe(audio)   a whole utterance at once

Not thread-safe: the service's one work thread owns the GPU (T13).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np


@dataclass(frozen=True)
class Transcript:
    language: str           # detected (or the one asked for); "" when nothing was said
    text: str
    final: bool = False     # the end of the utterance: this text won't change


class TooLong(Exception):
    """An utterance passed the session's limit (T13: 60 s)."""


class STTSession(Protocol):
    def feed(self, pcm: np.ndarray) -> Transcript | None:
        """More audio (16 kHz mono, float32 or int16). A partial transcript when it ran a pass."""
        ...

    def finish(self) -> Transcript:
        """The end of speech: the final transcript."""
        ...


class STTEngine(Protocol):
    sample_rate: int        # 16,000

    def load(self) -> None: ...

    def warm_up(self) -> None: ...

    def languages(self) -> list[str]: ...

    def session(self, language: str | None = None) -> STTSession: ...

    def transcribe(self, audio: np.ndarray, language: str | None = None) -> Transcript: ...
