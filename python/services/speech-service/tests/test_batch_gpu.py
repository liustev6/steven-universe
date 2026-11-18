"""The TTS's batch (batch.py) on the real model and GPU (marked `gpu`: run on CT 108, skipped elsewhere).

Greedy throughout, so runs repeat exactly. The key properties: a row's frames don't depend on what the
other rows do (bit for bit), and each row is close to Qwen's exact path (the near-tie standard)."""

import os

import pytest

torch = pytest.importorskip("torch")
pytestmark = [pytest.mark.gpu,
              pytest.mark.skipif(not (os.environ.get("TTS_MODEL") and torch.cuda.is_available()),
                                 reason="needs the model (TTS_MODEL) and a GPU")]

A = ("ryan", "Hi, how can I help you today?")
B = ("vivian", "好的，我现在帮你把客厅的灯关掉。")
C = ("aiden", "The kitchen lights are on, and the door is locked.")
D = ("serena", "今天西雅图多云，最高气温十二度。")


@pytest.fixture(scope="module")
def engine():
    from app.tts_engine.engine import QwenTTSEngine

    e = QwenTTSEngine(os.environ["TTS_MODEL"], predictor="ours-fused", talker="ours-graph")
    e.load()
    return e


@pytest.fixture(scope="module")
def batch(engine):
    return engine.batch(slots=2)


def run(batch, schedule, engine, *, max_steps=2000):
    """`schedule`: [(the step to join at, (voice, text))]. Each sentence's frames, (n, 16), in order."""
    prompts = [(at, engine.prompt(text, voice, sample=False)) for at, (voice, text) in schedule]
    frames, slot_of, done = {i: [] for i in range(len(prompts))}, {}, set()
    for step in range(max_steps):
        for i, (at, prompt) in enumerate(prompts):
            if at == step:
                slot_of[batch.join(prompt)] = i
        out, ended = batch.step()
        for slot, frame in out.items():
            frames[slot_of[slot]].append(frame)
        for slot in ended:
            done.add(slot_of.pop(slot))
        if len(done) == len(prompts):
            return [torch.stack(frames[i]).cpu() for i in range(len(prompts))]
    raise AssertionError("didn't finish")


def test_a_row_does_not_depend_on_the_others(batch, engine):
    a_alone, = run(batch, [(0, A)], engine)
    b_alone, = run(batch, [(0, B)], engine)
    a_with, b_with = run(batch, [(0, A), (5, B)], engine)              # B joins 5 steps after A
    assert torch.equal(a_with, a_alone)
    assert torch.equal(b_with, b_alone)


def test_each_row_is_close_to_qwens_exact_path(batch, engine):
    """The exact talker (our loop, unfused) fed each row's frames: code 1's scores, step by step, by
    the near-tie standard (the batch's matrix multiplies at 2 rows may round differently)."""
    from app.tts_engine.talker import TalkerLoop
    from tests.test_tts_gpu import bf16_steps

    exact = TalkerLoop(engine._talker)
    for voice, text in (A, B, C):
        prompt = engine.prompt(text, voice, sample=False)
        got, frames = [], []
        slot = batch.join(prompt, trace=got)
        while True:
            out, ended = batch.step()
            frames.append(out[slot])
            if slot in ended:
                break
        want = []
        with torch.inference_mode():
            exact.generate(**prompt, predictor=None, emit=lambda codes: None,
                           forced=[f[None] for f in frames], trace=want)
        want, got = torch.cat(want), torch.cat(got)
        n = min(len(want), len(got))
        want, got = want[:n], got[:n]
        top2 = want.topk(2, dim=-1).values
        differs = want.argmax(-1) != got.argmax(-1)
        near_tie = (top2[:, 0] - top2[:, 1]) <= 2 * bf16_steps(top2[:, 0])
        assert not (differs & ~near_tie).any(), text
        assert differs.float().mean() < 0.02, text


def test_sentences_end_and_the_limit_holds(batch, engine):
    from app.tts_engine.engine import max_frames

    a, = run(batch, [(0, A)], engine)
    assert 25 < len(a) < 80                                            # ~3 s of speech, ended by itself
    capped, = run(batch, [(0, ("ryan", "Warming up."))], engine)       # greedy, never says the end code
    assert len(capped) <= max_frames("Warming up.")


def test_full_then_free(batch, engine):
    from app.tts_engine.batch import Full

    p = engine.prompt(A[1], A[0], sample=False)
    s1, s2 = batch.join(p), batch.join(p)
    with pytest.raises(Full):
        batch.join(p)
    batch.leave(s1)
    assert batch.join(p) == s1
    batch.leave(s1)
    batch.leave(s2)
    assert batch.free() == [0, 1]


def test_four_slots(engine):
    four = engine.batch(slots=4)
    alone = [run(four, [(0, s)], engine)[0] for s in (A, B, C, D)]
    together = run(four, [(0, A), (2, B), (4, C), (6, D)], engine)
    for a, t in zip(alone, together):
        assert torch.equal(a, t)
