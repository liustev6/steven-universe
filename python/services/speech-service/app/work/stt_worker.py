"""The STT's work thread (speech-service.md T13; spikes/12).

One thread owns the STT's batch (`STTBatch`, stt_engine/batch.py); sessions talk to it through
thread-safe calls. It ticks the batch every `tick` seconds, and **at once when an utterance ends**,
so a final never waits for the next tick (spikes/12: up to 100 ms of a final's latency was waiting).

    open(language, sink, context) → a handle   one utterance; `sink(kind, payload)` gets ("partial", Transcript)
                                      …, then ("final", Transcript), or ("error", message); ("limit",
                                      None) first if its audio reached the limit (the final is then
                                      what was heard up to it; later audio is dropped)
    audio(handle, pcm)                its audio as it arrives (16 kHz)
    end(handle)                       the speaker stopped: its final, at once
    cancel(handle)                    stop now: its slot is free
"""

from __future__ import annotations

import itertools
import logging
import queue
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

from app.stt_engine.base import TooLong

logger = logging.getLogger(__name__)


@dataclass(eq=False)
class Utterance:
    id: int
    language: str | None
    sink: Callable[[str, object], None]
    context: str = ""                                 # what the speech is likely about (T18)
    slot: int | None = None
    ended: bool = False
    pending: list = field(default_factory=list)       # what arrived while it waited for a slot


class STTWorker:
    def __init__(self, batch, *, tick: float = 0.1):
        self.batch, self.tick = batch, tick
        self._inbox: queue.Queue = queue.Queue()
        self._ids = itertools.count(1)
        self._thread = threading.Thread(target=self._run, name="stt-work", daemon=True)
        self._stopping = False
        self.finals = 0

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stopping = True
        self._inbox.put(None)
        self._thread.join(timeout=5)

    def open(self, language: str | None, sink, context: str = "") -> Utterance:
        u = Utterance(next(self._ids), language, sink, context)
        self._inbox.put(("open", u, None))
        return u

    def audio(self, u: Utterance, pcm) -> None:
        self._inbox.put(("audio", u, pcm))

    def end(self, u: Utterance) -> None:
        self._inbox.put(("end", u, None))

    def cancel(self, u: Utterance) -> None:
        self._inbox.put(("cancel", u, None))

    def busy(self) -> int:
        return sum(1 for s in self.batch.sessions if s is not None)

    # ---- the loop (the work thread) ------------------------------------------------------------------
    def _run(self) -> None:
        waiting: deque[Utterance] = deque()
        by_slot: dict[int, Utterance] = {}
        next_tick = time.monotonic() + self.tick
        while not self._stopping:
            now_due = self._take(waiting, by_slot, until=next_tick)
            while waiting and self.batch.free():
                u = waiting.popleft()
                try:
                    u.slot = self.batch.join(u.language, u.context)
                except Exception as exc:                            # an unknown language: this one only
                    u.sink("error", str(exc))
                    continue
                by_slot[u.slot] = u
                for kind, pcm in u.pending:                         # audio that arrived while it waited
                    now_due |= self._apply(u, kind, pcm, by_slot)
            if now_due or time.monotonic() >= next_tick:
                next_tick = time.monotonic() + self.tick
                if by_slot:
                    self._tick(by_slot)

    def _take(self, waiting, by_slot, *, until: float) -> bool:
        """Everything in the inbox until the next tick; True if an utterance ended (tick now)."""
        ended = False
        while True:
            timeout = max(0.0, until - time.monotonic())
            try:
                item = self._inbox.get(timeout=timeout) if not ended else self._inbox.get_nowait()
            except queue.Empty:
                return ended
            if item is None:
                return ended
            kind, u, pcm = item
            if kind == "open":
                waiting.append(u)
            elif u.slot is None:                                      # still waiting for a slot
                if kind == "cancel":
                    if u in waiting:
                        waiting.remove(u)
                else:
                    u.pending.append((kind, pcm))
            else:
                ended |= self._apply(u, kind, pcm, by_slot)

    def _apply(self, u: Utterance, kind: str, pcm, by_slot) -> bool:
        """One message for an utterance with a slot; True if it ended now (tick at once for its final)."""
        if u.slot not in by_slot:
            return False
        if kind == "audio":
            if u.ended:
                return False                                          # past the limit, or after the end
            try:
                self.batch.add(u.slot, pcm)
            except TooLong:                                           # the limit: what was heard is the final
                u.ended = True
                self.batch.end(u.slot)
                u.sink("limit", None)
                return True
        elif kind == "end" and not u.ended:
            u.ended = True
            self.batch.end(u.slot)
            return True
        elif kind == "cancel":
            self.batch.leave(u.slot)
            del by_slot[u.slot]
        return False

    def _tick(self, by_slot: dict) -> None:
        try:
            out = self.batch.tick()
        except Exception as exc:                                     # the GPU failed: every utterance ends
            logger.exception("an STT pass failed")
            for slot, u in list(by_slot.items()):
                self.batch.leave(slot)
                u.sink("error", str(exc))
            by_slot.clear()
            return
        for slot, transcript in out.items():
            u = by_slot.get(slot)
            if u is None:
                continue
            if transcript.final:
                del by_slot[slot]
                self.finals += 1
                u.sink("final", transcript)
            else:
                u.sink("partial", transcript)
