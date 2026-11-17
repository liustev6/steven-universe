"""The engine: what the service asks of a text-to-speech model (tts-service.md T3, T11).

    load()        the weights onto the GPU (once, at start)
    warm_up()     one throwaway generation: CUDA starts up, graphs are captured; only then is the
                  service healthy (T3)
    voices()      the preset voices, with their languages
    stream(...)   one sentence → its audio, as float32 mono chunks at `sample_rate`, each yielded as
                  soon as it's made; stops between frames once `cancel` is set (barge-in, T6)

The service talks only to this interface, so the engine behind it (plain PyTorch, CUDA graphs, a
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


class Engine(Protocol):
    sample_rate: int

    def load(self) -> None: ...

    def warm_up(self) -> None: ...

    def voices(self) -> list[Voice]: ...

    def stream(self, text: str, voice: str, language: str | None = None, *, sample: bool = True,
               cancel: threading.Event | None = None) -> Iterator[np.ndarray]:
        """`language` None: the voice's own. `sample` False: no randomness (greedy decoding), so
        two engines can be compared code for code."""
        ...
