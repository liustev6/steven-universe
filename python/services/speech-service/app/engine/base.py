"""The engines: what the service asks of its two models (speech-service.md T3, T11, T12).

TTSEngine, text to speech:
    load()        the weights onto the GPU (once, at start)
    warm_up()     one throwaway generation: CUDA starts up, graphs are captured; only then is the
                  service healthy (T3)
    voices()      the preset voices, with their languages
    stream(...)   one sentence → its audio, as float32 mono chunks at `sample_rate`, each yielded as
                  soon as it's made; stops between frames once `cancel` is set (barge-in, T6)

STTEngine, speech to text:
    load(), warm_up()   as above
    languages()         the languages it recognizes (it detects which, unless told)
    session(language)   one utterance, streamed in: `feed(pcm)` 16 kHz audio as it arrives (a partial
                        `Transcript` whenever a pass ran), `finish()` at the end of speech (the final)
    transcribe(audio)   a whole utterance at once

The service talks only to these interfaces, so the engine behind it (plain PyTorch, CUDA graphs, a
future model) changes without touching the service. Engines are not thread-safe: the service runs
one at a time, on its one GPU worker.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Protocol

import numpy as np


@dataclass(frozen=True)
class Voice:
    name: str               # what callers send, e.g. "vivian"
    language: str           # its native language, e.g. "Chinese"


class Cancelled(Exception):
    """`stream` stopped because its `cancel` was set."""


@dataclass(frozen=True)
class Transcript:
    language: str           # detected (or the one asked for); "" when nothing was said
    text: str
    final: bool = False     # the end of the utterance: this text won't change


class TooLong(Exception):
    """An utterance passed the session's limit (T13: 60 s)."""


class TTSEngine(Protocol):
    sample_rate: int

    def load(self) -> None: ...

    def warm_up(self) -> None: ...

    def voices(self) -> list[Voice]: ...

    def stream(self, text: str, voice: str, language: str | None = None, *, sample: bool = True,
               cancel: threading.Event | None = None) -> Iterator[np.ndarray]:
        """`language` None: the voice's own. `sample` False: no randomness (greedy decoding), so
        two engines can be compared code for code."""
        ...


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
