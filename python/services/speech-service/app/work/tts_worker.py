"""The TTS's work thread (speech-service.md T13; the pattern measured in spikes/12).

One thread owns the TTS's batch (`TTSBatch`, tts_engine/batch.py); the sessions only talk to it
through `say()` and `cancel()`, which are thread-safe. Per slot, a decode thread turns that slot's
frames into audio (the codec's chunked decode on the high-priority stream) and hands it to the
sentence's `sink`.

    say(text, voice, language, sink) → a job     one sentence; `sink(kind, payload)` gets, in order,
                                                 ("audio", float32 24 kHz chunk) …, then ("done", None)
                                                 or ("error", message); called from worker threads
    cancel(job)                                  stop it now: its slot is free at the next step

The loop: take new sentences and cancels; prepare each new sentence's prompt and join it into a free
slot (sentences wait in order while every slot is busy); step the batch, handing each frame over
right after the predictor (with a CUDA event, so its decode starts during the talker's step); a slot
whose sentence ended gets "end" to its decode thread. Idle, it sleeps until there is work.
"""

from __future__ import annotations

import itertools
import logging
import queue
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

from app.tts_engine.chunker import ChunkDecoder

logger = logging.getLogger(__name__)
Sink = Callable[[str, object], None]


@dataclass
class Job:
    id: int
    text: str
    voice: str
    language: str | None
    sink: Sink
    cancelled: bool = field(default=False, repr=False)


class TTSWorker:
    def __init__(self, batch, prompt: Callable, decode: Callable[[list], np.ndarray], gpu, *,
                 chunks=(1, 1, 2, 4), context: int = 25):
        """`batch`: a TTSBatch (or a fake: join, step(on_frames), leave). `prompt(text, voice, language)`:
        a sentence's prompt. `decode(frames)`: the codec, frames → audio. `gpu`: work.gpu."""
        self.batch, self.prompt, self.decode, self.gpu = batch, prompt, decode, gpu
        self.chunks, self.context = chunks, context
        self._inbox: queue.Queue = queue.Queue()
        self._ids = itertools.count(1)
        self._decoders = [_Decoder(self, i) for i in range(batch.slots)]
        self._thread = threading.Thread(target=self._run, name="tts-work", daemon=True)
        self._stopping = False
        self.sentences_done = 0

    def start(self) -> None:
        for d in self._decoders:
            d.thread.start()
        self._thread.start()

    def stop(self) -> None:
        self._stopping = True
        self._inbox.put(None)
        self._thread.join(timeout=5)
        for d in self._decoders:
            d.inbox.put(None)

    def say(self, text: str, voice: str, language: str | None, sink: Sink) -> Job:
        job = Job(next(self._ids), text, voice, language, sink)
        self._inbox.put(("say", job))
        return job

    def cancel(self, job: Job) -> None:
        job.cancelled = True
        self._inbox.put(("cancel", job))

    def busy(self) -> int:
        return sum(1 for s in self.batch.state if s is not None)

    # ---- the loop (the work thread) ------------------------------------------------------------------
    def _run(self) -> None:
        waiting: deque[Job] = deque()
        slot_job: dict[int, Job] = {}
        with self.gpu.inference(), self.gpu.generation():
            while not self._stopping:
                self._take(waiting, slot_job, block=not slot_job and not waiting)
                while waiting and self.batch.free():
                    job = waiting.popleft()
                    try:
                        slot = self.batch.join(self.prompt(job.text, job.voice, job.language))
                    except Exception as exc:                       # a bad voice, too long: this job only
                        logger.exception("sentence %d failed to start", job.id)
                        job.sink("error", str(exc))
                        continue
                    slot_job[slot] = job
                    self._decoders[slot].inbox.put(("start", job))
                if not slot_job:
                    continue

                def hand_over(frames):
                    ready = self.gpu.event()
                    for slot, frame in frames.items():
                        self._decoders[slot].inbox.put(("frame", frame, ready))

                try:
                    _, ended = self.batch.step(on_frames=hand_over)
                except Exception as exc:                           # the GPU failed: every sentence ends
                    logger.exception("a TTS step failed")
                    for slot, job in slot_job.items():
                        self.batch.leave(slot)
                        self._decoders[slot].inbox.put(("error", str(exc)))
                    slot_job.clear()
                    continue
                for slot in ended:
                    slot_job.pop(slot)
                    self._decoders[slot].inbox.put(("end",))

    def _take(self, waiting: deque, slot_job: dict, *, block: bool) -> None:
        """New sentences and cancels: all that's in the inbox (waiting for one if there's nothing to do)."""
        while True:
            try:
                item = self._inbox.get(block=block)
            except queue.Empty:
                return
            block = False
            if item is None:
                return
            kind, job = item
            if kind == "say":
                waiting.append(job)
            elif kind == "cancel":
                if job in waiting:
                    waiting.remove(job)
                for slot, j in list(slot_job.items()):
                    if j is job:
                        self.batch.leave(slot)
                        del slot_job[slot]
                        self._decoders[slot].inbox.put(("cancel",))


class _Decoder:
    """One slot's audio: its sentence's frames → chunks (1, 1, 2, then 4 frames) → the sink."""

    def __init__(self, worker: TTSWorker, slot: int):
        self.worker, self.slot = worker, slot
        self.inbox: queue.Queue = queue.Queue()
        self.thread = threading.Thread(target=self._run, name=f"tts-decode-{slot}", daemon=True)

    def _run(self) -> None:
        w = self.worker
        job, chunker = None, None
        with w.gpu.inference(), w.gpu.decoding():
            while (item := self.inbox.get()) is not None:
                kind = item[0]
                try:
                    if kind == "start":
                        job, chunker = item[1], ChunkDecoder(w.decode, chunks=w.chunks, context=w.context)
                    elif kind == "frame" and job is not None and not job.cancelled:
                        w.gpu.wait(item[2])
                        if (audio := chunker.add(item[1])) is not None:
                            job.sink("audio", audio)
                    elif kind == "end" and job is not None:
                        if not job.cancelled and (audio := chunker.flush()) is not None:
                            job.sink("audio", audio)
                        if not job.cancelled:
                            job.sink("done", None)
                            w.sentences_done += 1
                        job = None
                    elif kind in ("cancel", "error") and job is not None:
                        if kind == "error":
                            job.sink("error", item[1])
                        job = None
                except Exception as exc:                           # a decode failed: this sentence only
                    logger.exception("decoding slot %d failed", self.slot)
                    if job is not None:
                        job.sink("error", str(exc))
                        job.cancelled = True
