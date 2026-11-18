"""The two work threads (app/work), with fakes for the models: no GPU."""

import threading
import time

import numpy as np

from app.stt_engine.batch import STTBatch
from app.tts_engine.chunker import SAMPLES_PER_FRAME as SPF
from app.work.gpu import NoGPU
from app.work.stt_worker import STTWorker
from app.work.tts_worker import TTSWorker
from tests.test_stt_batch import FakeEngine


# ---- TTS -------------------------------------------------------------------------------------------
class FakeReplyText:
    """A piece's words are its tokens; the voice "x" can't start."""

    def prompt(self, text, voice, language):
        if voice == "x":
            raise ValueError("no voice")
        return {"words": text.split()}

    def more(self, text, after):
        return text.split()

    def end(self):
        return "END"

    def limit(self, text):
        return 10_000


class FakeTTSBatch:
    """Each word of a reply is one frame, made one a step; a row whose words run out before its end mark
    waits (as a streamed row waiting for text); it ends on the step after its last word once finished."""

    def __init__(self, slots=2):
        self.slots, self.state, self.steps, self.joins = slots, [None] * slots, 0, []

    def free(self):
        return [i for i, s in enumerate(self.state) if s is None]

    def ready(self):
        return [b for b, s in enumerate(self.state) if s is not None and (s["words"] or s["done"])]

    def join(self, prompt, slot=None):
        b = slot if slot is not None else self.free()[0]
        assert self.state[b] is None
        self.state[b] = {"words": list(prompt["words"]), "made": 0, "done": False}
        self.joins.append(b)
        return b

    def feed(self, b, words, limit=None):
        assert not self.state[b]["done"]
        self.state[b]["words"] += words

    def finish(self, b, end):
        self.state[b]["done"] = True

    def leave(self, b):
        self.state[b] = None

    def step(self, on_frames=None):
        time.sleep(0.002)
        self.steps += 1
        ready = self.ready()
        frames = {b: self.state[b]["made"] for b in ready if self.state[b]["words"]}
        if on_frames and frames:
            on_frames(frames)
        ended = []
        for b in ready:
            s = self.state[b]
            if s["words"]:
                s["words"].pop(0)
                s["made"] += 1
            if s["done"] and not s["words"]:
                ended.append(b)
                self.state[b] = None
        return frames, ended


class Collect:
    def __init__(self):
        self.items, self.done = [], threading.Event()

    def __call__(self, kind, payload):
        self.items.append((kind, payload))
        if kind in ("done", "error", "final"):
            self.done.set()

    def samples(self):
        return sum(len(p) for k, p in self.items if k == "audio")


def tts_worker(slots=2, **kw):
    w = TTSWorker(FakeTTSBatch(slots), text=FakeReplyText(),
                  decode=lambda frames: np.zeros(len(frames) * SPF, dtype=np.float32), gpu=NoGPU(), **kw)
    w.start()
    return w


def test_a_reply_comes_out_whole_then_done():
    w = tts_worker()
    c = Collect()
    w.say("one two three four five six seven", "ryan", None, c)
    assert c.done.wait(5)
    assert c.items[-1] == ("done", None) and c.samples() == 7 * SPF
    assert [len(p) // SPF for k, p in c.items if k == "audio"] == [1, 1, 2, 3]   # 1, 1, 2, then the rest
    w.stop()


def test_a_reply_fed_piece_by_piece_is_one_generation_that_waits_for_its_text():
    w = tts_worker()
    c = Collect()
    job = w.open("ryan", None, c)
    w.feed(job, "Sure,")
    time.sleep(0.05)                                     # its one word spoken; then it waits, not ends
    assert not c.done.is_set() and w.batch.state[0] is not None
    w.feed(job, "tomorrow looks cloudy.")
    w.feed(job, "And then some rain")
    w.end(job)
    assert c.done.wait(5) and c.items[-1] == ("done", None)
    assert c.samples() == 8 * SPF and w.batch.joins == [0]   # every word, one generation
    w.stop()


def test_a_long_reply_continues_in_new_generations_on_its_row():
    w = tts_worker(generation_frames=10)                 # ~11 words a generation (0.9 frames a character)
    c = Collect()
    job = w.open("ryan", None, c)
    for i in range(6):
        w.feed(job, f"piece {i} has five words")         # 5 words, ~22 characters each
    w.end(job)
    assert c.done.wait(5) and [k for k, _ in c.items].count("done") == 1
    assert c.samples() == 30 * SPF                       # nothing lost at the seams
    assert len(w.batch.joins) == 6 and set(w.batch.joins) == {0}   # each piece a generation here, same row
    w.stop()


def test_two_at_once_and_a_third_waits_its_turn():
    w = tts_worker(slots=2)
    cs = [Collect() for _ in range(3)]
    for i, c in enumerate(cs):
        w.say(" ".join(["w"] * (20 + i)), "ryan", None, c)
    assert all(c.done.wait(5) for c in cs)
    assert [c.samples() // SPF for c in cs] == [20, 21, 22]
    w.stop()


def test_a_reply_waiting_for_text_doesnt_hold_up_the_other():
    w = tts_worker(slots=2)
    slow, fast = Collect(), Collect()
    job = w.open("ryan", None, slow)
    w.feed(job, "one")                                   # then nothing more for a while
    w.say("a b c d e", "ryan", None, fast)
    assert fast.done.wait(5) and fast.samples() == 5 * SPF and not slow.done.is_set()
    w.end(job)
    assert slow.done.wait(5) and slow.samples() == 1 * SPF
    w.stop()


def test_cancel_stops_it_and_frees_the_slot():
    w = tts_worker(slots=1)
    long, after = Collect(), Collect()
    job = w.say(" ".join(["w"] * 2000), "ryan", None, long)
    w.say("a b c", "ryan", None, after)                  # waits: the only slot is busy
    time.sleep(0.05)
    w.cancel(job)
    assert after.done.wait(5) and after.samples() == 3 * SPF
    assert ("done", None) not in long.items
    w.stop()


def test_an_empty_reply_is_done_at_once():
    w = tts_worker()
    c = Collect()
    job = w.open("ryan", None, c)
    w.end(job)
    assert c.done.wait(5) and c.items == [("done", None)]
    w.stop()


def test_a_reply_that_cant_start_errors_alone():
    w = tts_worker()
    bad, good = Collect(), Collect()
    w.say("a b", "x", None, bad)
    w.say("a b", "ryan", None, good)
    assert bad.done.wait(5) and bad.items[-1][0] == "error"
    assert good.done.wait(5) and good.items[-1] == ("done", None)
    w.stop()


# ---- STT -------------------------------------------------------------------------------------------
def stt_worker(slots=2, tick=0.1):
    w = STTWorker(STTBatch(FakeEngine(), slots=slots), tick=tick)
    w.start()
    return w


def chunks(seconds):
    return [np.zeros(1600, dtype=np.float32) for _ in range(int(seconds * 10))]


def test_partials_then_the_final_at_once_after_the_end():
    w = stt_worker(tick=0.5)                               # a long tick: the final mustn't wait for it
    c = Collect()
    u = w.open(None, c)
    for pcm in chunks(2.3):
        w.audio(u, pcm)
    time.sleep(0.6)                                        # a tick: partials for the passes due
    start = time.monotonic()
    w.end(u)
    assert c.done.wait(2)
    assert time.monotonic() - start < 0.2
    kinds = [k for k, _ in c.items]
    assert kinds[-1] == "final" and "partial" in kinds
    assert c.items[-1][1].text == "hello world 2s"
    w.stop()


def test_audio_sent_while_waiting_for_a_slot_isnt_lost():
    w = stt_worker(slots=1)
    first, second = Collect(), Collect()
    u1, u2 = w.open(None, first), w.open(None, second)
    for pcm in chunks(1.5):
        w.audio(u2, pcm)                                   # u2 has no slot yet
    w.end(u2)
    w.end(u1)                                              # u1 (no audio) ends: its slot frees
    assert first.done.wait(2) and second.done.wait(2)
    assert second.items[-1][1].text == "hello world 1s"
    w.stop()


def test_cancel_and_too_long():
    w = stt_worker()
    a, b = Collect(), Collect()
    ua, ub = w.open(None, a), w.open(None, b)
    w.audio(ua, np.zeros(16000, dtype=np.float32))
    w.cancel(ua)
    w.audio(ub, np.zeros(30 * 16000, dtype=np.float32))
    w.audio(ub, np.zeros(31 * 16000, dtype=np.float32))    # past the 60 s limit: what was heard is final
    w.audio(ub, np.zeros(16000, dtype=np.float32))         # later audio is dropped
    assert b.done.wait(2)
    assert [k for k, _ in b.items][-2:] == ["limit", "final"] and b.items[-1][1].text == "hello world 30s"
    time.sleep(0.2)
    assert not a.done.is_set() and w.busy() == 0
    w.stop()
