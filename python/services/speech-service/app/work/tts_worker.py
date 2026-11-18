"""The TTS's work thread (speech-service.md T13, T19; the pattern measured in spikes/12).

One thread owns the TTS's batch (`TTSBatch`, tts_engine/batch.py); the sessions only talk to it
through the methods below, which are thread-safe. **A job is one reply, spoken as one generation fed
its text as it's written** (Qwen's streaming mode, spikes/16): no reset of the voice between sentences,
the model placing its own pauses. Per slot, a decode thread turns that slot's frames into audio (the
codec's chunked decode on the high-priority stream) and hands it to the reply's `sink`.

    open(voice, language, sink) → a job   one reply; `sink(kind, payload)` gets, in order,
                                          ("audio", float32 24 kHz chunk) …, then ("done", None)
                                          or ("error", message); called from worker threads
    feed(job, text)                       the next piece of its text: a whole sentence or clause, as
                                          the session's splitter cuts it (normalization needs whole
                                          numbers, so never half a word)
    end(job)                              its text is complete
    cancel(job)                           stop it now: its slot is free at the next step

The loop: take what the sessions sent; a reply with text and a free slot joins (its first piece's
prompt in Qwen's streaming mode); each reply's new pieces become its row's next text tokens (`feed`),
its end the end mark (`finish`); step the batch (a row whose next token isn't written yet waits),
handing each frame over right after the predictor (with a CUDA event, so its decode starts during the
talker's step). Idle, or every row waiting for text, it sleeps until something arrives.

**A reply longer than a row's cache** (2,048 positions: ~160 s of speech) is spoken in several
generations: once a generation's text is expected to make ~90 s of speech, it gets its end mark, and
the rest of the reply starts a new generation on the same row when it ends (a rare seam, at a
sentence end). A generation that ends before its reply has (its limit, or the model ending early)
continues the same way.
"""

from __future__ import annotations

import itertools
import logging
import queue
import re
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

import numpy as np

from app.tts_engine.chunker import ChunkDecoder

logger = logging.getLogger(__name__)
Sink = Callable[[str, object], None]
CJK = re.compile(r"[぀-ヿ㐀-鿿가-힯]")
GENERATION_FRAMES = 1100                 # a generation's text, in expected frames (~90 s), before a new one


def expected_frames(text: str) -> float:
    """Frames a text is expected to take at a natural pace (spikes/14: Chinese ~4.7 characters a second,
    English ~14 characters a second; 12.5 frames a second)."""
    cjk = len(CJK.findall(text))
    return cjk * 2.7 + (len(text) - cjk) * 0.9


class ReplyText(Protocol):
    """A reply's text as the model reads it (main.py: Qwen's engine with normalization)."""

    def prompt(self, text: str, voice: str, language: str | None) -> object: ...   # the first piece's prompt
    def more(self, text: str, after: str) -> object: ...      # the next piece, after `after` in this generation
    def end(self) -> object: ...                              # the end mark
    def limit(self, text: str) -> int: ...                    # a generation's frame limit for its text so far


@dataclass(eq=False)
class Job:
    id: int
    voice: str
    language: str | None
    sink: Sink
    texts: deque = field(default_factory=deque)   # pieces not yet in a generation
    ended: bool = False                           # the reply's text is complete
    slot: int | None = None                       # its generation's row, while one runs
    last_slot: int | None = None                  # its last generation's row (its decoder)
    said: str = ""                                # this generation's text so far
    expected: float = 0.0                         # its expected frames
    finished: bool = False                        # this generation has its end mark
    decoding: bool = False                        # its decoder has started
    cancelled: bool = field(default=False, repr=False)


class TTSWorker:
    def __init__(self, batch, text: ReplyText, decode: Callable[[list], np.ndarray], gpu, *,
                 chunks=(1, 1, 2, 4), context: int = 25, generation_frames: float = GENERATION_FRAMES):
        """`batch`: a TTSBatch (or a fake: join, feed, finish, ready, step(on_frames), leave, free).
        `text`: ReplyText. `decode(frames)`: the codec, frames → audio. `gpu`: work.gpu."""
        self.batch, self.text, self.decode, self.gpu = batch, text, decode, gpu
        self.chunks, self.context, self.generation_frames = chunks, context, generation_frames
        self._inbox: queue.Queue = queue.Queue()
        self._ids = itertools.count(1)
        self._decoders = [_Decoder(self, i) for i in range(batch.slots)]
        self._thread = threading.Thread(target=self._run, name="tts-work", daemon=True)
        self._stopping = False
        self.sentences_done = 0                   # pieces spoken (for the status page)
        self.replies_done = 0

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

    # ---- the sessions' side (any thread) ---------------------------------------------------------------
    def open(self, voice: str, language: str | None, sink: Sink) -> Job:
        job = Job(next(self._ids), voice, language, sink)
        self._inbox.put(("open", job, None))
        return job

    def feed(self, job: Job, text: str) -> None:
        self._inbox.put(("feed", job, text))

    def end(self, job: Job) -> None:
        self._inbox.put(("end", job, None))

    def cancel(self, job: Job) -> None:
        job.cancelled = True
        self._inbox.put(("cancel", job, None))

    def say(self, text: str, voice: str, language: str | None, sink: Sink) -> Job:
        """A whole reply at once (warm-up, tests)."""
        job = self.open(voice, language, sink)
        self.feed(job, text)
        self.end(job)
        return job

    def busy(self) -> int:
        return sum(1 for s in self.batch.state if s is not None)

    # ---- the loop (the work thread) --------------------------------------------------------------------
    def _run(self) -> None:
        jobs: deque[Job] = deque()                # open replies, in order: running or waiting for a slot
        with self.gpu.inference(), self.gpu.generation():
            while not self._stopping:
                self._take(jobs, block=not self.batch.ready() and not self._can_start(jobs))
                for job in list(jobs):            # in order: earlier replies take the free slots first
                    if job.slot is None and job.texts and self.batch.free():
                        self._start(job, jobs)
                    elif job.slot is None and job.ended and not job.texts:   # all its text spoken (or none)
                        self._done(job, jobs)
                for job in [j for j in jobs if j.slot is not None]:
                    self._feed(job)
                if not self.batch.ready():
                    continue

                def hand_over(frames):
                    ready = self.gpu.event()
                    for slot, frame in frames.items():
                        self._decoders[slot].inbox.put(("frame", frame, ready))

                try:
                    _, ended = self.batch.step(on_frames=hand_over)
                except Exception as exc:                           # the GPU failed: every reply ends
                    logger.exception("a TTS step failed")
                    for job in [j for j in jobs if j.slot is not None]:
                        self.batch.leave(job.slot)
                        self._decoders[job.slot].inbox.put(("error", str(exc)))
                        jobs.remove(job)
                    continue
                for slot in ended:
                    job = next(j for j in jobs if j.slot == slot)
                    job.slot = None                                # its next generation, if any, starts above
                    if job.ended and job.finished and not job.texts:
                        self._done(job, jobs, slot)
                    else:
                        self._decoders[slot].inbox.put(("cut",))  # more to say: the decoder flushes, carries on

    def _can_start(self, jobs) -> bool:
        return any(j.slot is None and ((j.texts and self.batch.free()) or (j.ended and not j.texts)) for j in jobs)

    def _start(self, job: Job, jobs: deque) -> None:
        """A generation for the reply: its next piece's prompt into a free slot (its last row, if free)."""
        text = job.texts.popleft()
        preferred = job.last_slot
        slot = preferred if preferred in self.batch.free() else None
        try:
            job.slot = self.batch.join(self.text.prompt(text, job.voice, job.language), slot=slot)
        except Exception as exc:                                   # a bad voice, too long: this reply only
            logger.exception("reply %d failed to start", job.id)
            job.sink("error", str(exc))
            jobs.remove(job)
            return
        job.said, job.expected, job.finished = text, expected_frames(text), False
        if not job.decoding or job.slot != preferred:
            if job.decoding and preferred is not None:
                self._decoders[preferred].inbox.put(("release",))  # its audio so far went out at the cut
            self._decoders[job.slot].inbox.put(("start", job))
            job.decoding = True
        job.last_slot = job.slot
        self.sentences_done += 1

    def _feed(self, job: Job) -> None:
        """A running reply's new pieces into its row; its end mark when its text is complete, or when
        this generation has enough (the rest: a new generation)."""
        if job.finished:
            return
        while job.texts and job.expected + expected_frames(job.texts[0]) <= self.generation_frames:
            text = job.texts.popleft()
            self.batch.feed(job.slot, self.text.more(text, job.said), limit=self.text.limit(job.said + " " + text))
            job.said += " " + text
            job.expected += expected_frames(text)
            self.sentences_done += 1
        if (job.ended and not job.texts) or job.texts:             # complete, or the rest needs a new generation
            self.batch.finish(job.slot, self.text.end())
            job.finished = True

    def _done(self, job: Job, jobs: deque, slot: int | None = None) -> None:
        """All the reply's audio is made: its decoder flushes the last of it and says done."""
        jobs.remove(job)
        if job.decoding:
            self._decoders[job.last_slot].inbox.put(("end",))
        else:
            job.sink("done", None)                                 # a reply with no text: nothing to decode
        self.replies_done += 1

    def _take(self, jobs: deque, *, block: bool) -> None:
        """What the sessions sent: all that's in the inbox (waiting for one if there's nothing to do)."""
        while True:
            try:
                item = self._inbox.get(block=block)
            except queue.Empty:
                return
            block = False
            if item is None:
                return
            kind, job, text = item
            if kind == "open":
                jobs.append(job)
            elif job not in jobs:
                continue                                           # already done or cancelled
            elif kind == "feed":
                job.texts.append(text)
            elif kind == "end":
                job.ended = True
            elif kind == "cancel":
                jobs.remove(job)
                if job.slot is not None:
                    self.batch.leave(job.slot)
                if job.decoding:
                    self._decoders[job.last_slot].inbox.put(("cancel",))


class _Decoder:
    """One slot's audio: its reply's frames → chunks (1, 1, 2, then 4 frames) → the sink."""

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
                    elif kind == "cut" and job is not None:          # one generation done, the reply goes on
                        if not job.cancelled and (audio := chunker.flush()) is not None:
                            job.sink("audio", audio)
                        chunker = ChunkDecoder(w.decode, chunks=w.chunks, context=w.context)
                    elif kind == "end" and job is not None:
                        if not job.cancelled and (audio := chunker.flush()) is not None:
                            job.sink("audio", audio)
                        if not job.cancelled:
                            job.sink("done", None)
                        job = None
                    elif kind == "release":                         # the reply moved to another row
                        job = None
                    elif kind in ("cancel", "error") and job is not None:
                        if kind == "error":
                            job.sink("error", item[1])
                        job = None
                except Exception as exc:                           # a decode failed: this reply only
                    logger.exception("decoding slot %d failed", self.slot)
                    if job is not None:
                        job.sink("error", str(exc))
                        job.cancelled = True
