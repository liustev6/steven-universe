"""The STT's batch (batch.py) without a model: a fake engine records each pass's rows."""

import numpy as np
import pytest

from app.stt_engine.batch import Full, STTBatch
from app.stt_engine.transcription import SAMPLE_RATE as SR
from app.stt_engine.transcription import StreamingTranscription
from tests.test_transcription import Tokenizer


class FakeEngine:
    def __init__(self):
        self.passes = []                             # each pass: [(seconds of audio, prompt, prefix)]

    def session(self, language=None, *, context="", decode="own"):
        return StreamingTranscription(None, Tokenizer(), lambda raw: (language or "English", raw))

    def prompt(self, language=None, context=""):
        return f"<{language or 'auto'}>" + (f"[{context}]" if context else "")

    def decode_batch(self, audios, prompts, prefixes):
        self.passes.append([(len(a) / SR, p, x) for a, p, x in zip(audios, prompts, prefixes)])
        return [f"hello world {len(a) // SR}s" for a in audios]  # a consistent model: starts with its prefix


def seconds(s):
    return np.zeros(int(s * SR), dtype=np.float32)


def batch(slots=2):
    e = FakeEngine()
    return STTBatch(e, slots=slots), e


def test_nothing_runs_until_something_is_due():
    b, e = batch()
    s = b.join()
    b.add(s, seconds(0.9))
    assert b.tick() == {} and e.passes == []


def test_a_due_utterance_pulls_in_one_nearly_due_but_not_one_barely_started():
    b, e = batch(slots=3)
    x, y, z = b.join(), b.join(), b.join()
    b.add(x, seconds(1.0))                           # due
    b.add(y, seconds(0.6))                           # 60% of its first second: nearly due
    b.add(z, seconds(0.3))                           # 30%: not yet
    out = b.tick()
    assert set(out) == {x, y}
    assert [round(sec, 1) for sec, _, _ in e.passes[0]] == [1.0, 0.6]


def test_a_final_comes_at_the_next_tick_and_frees_the_slot():
    b, e = batch()
    s = b.join()
    b.add(s, seconds(0.4))                           # not due: no pass yet
    b.end(s)
    out = b.tick()
    assert out[s].final and out[s].text == "hello world 0s"
    assert b.free() == [0, 1]


def test_a_final_with_no_new_audio_needs_no_pass():
    b, e = batch()
    s = b.join()
    b.add(s, seconds(1.0))
    b.tick()
    b.end(s)
    out = b.tick()
    assert out[s].final and out[s].text == "hello world 1s" and len(e.passes) == 1


def test_each_row_keeps_its_prompt_and_prefix():
    b, e = batch()
    en, zh = b.join(), b.join("Chinese")
    for _ in range(3):                               # the third pass has a prefix (none for the first 2)
        b.add(en, seconds(1.0)); b.add(zh, seconds(1.0))
        out = b.tick()
    rows = e.passes[-1]                              # "hello world 2s" (14) less its last 5: "hello wor"
    assert [(prompt, prefix) for _, prompt, prefix in rows] == [("<auto>hello wor", "hello wor"),
                                                                ("<Chinese>hello wor", "hello wor")]
    assert out[en].language == "English" and out[zh].language == "Chinese"


def test_full_then_free():
    b, e = batch()
    s1, s2 = b.join(), b.join()
    with pytest.raises(Full):
        b.join()
    b.leave(s1)
    assert b.join() == s1


def test_lined_up_passes_are_fewer_than_separate_ones():
    """Two utterances, the second starting 0.6 s after the first, 100 ms of audio at a time, ticking
    after each: their passes share instead of each running alone."""
    b, e = batch()
    x = b.join()
    for i in range(60):                              # 6 s
        b.add(x, seconds(0.1))
        if i == 6:
            y = b.join()
        if i > 6:
            b.add(y, seconds(0.1))
        b.tick()
    shared = sum(1 for p in e.passes if len(p) == 2)
    assert shared >= 3 and len(e.passes) < 6 + 5


def test_a_row_keeps_its_context_in_its_prompt():
    b = STTBatch(FakeEngine(), slots=2)
    s = b.join("Chinese", "Seattle downtown")
    assert b.prompts[s] == "<Chinese>[Seattle downtown]"
