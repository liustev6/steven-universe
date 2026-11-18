"""A reply as one streamed generation (T19; spikes/16) on the real model and GPU (marked `gpu`: run on
CT 108, skipped elsewhere). Greedy, so runs repeat exactly.

The properties the service rests on:
  - fed in pieces with waits between them (beside another busy row), a reply's frames are the same as
    with all its text fed at once: waiting changes nothing, bit for bit
  - it's Qwen's streaming mode: each frame's code 1 against Qwen's exact talker fed the same text,
    by the near-tie standard (the fused kernels may round differently)
  - the reply ends by itself after its end mark"""

import os

import pytest

torch = pytest.importorskip("torch")
pytestmark = [pytest.mark.gpu,
              pytest.mark.skipif(not (os.environ.get("TTS_MODEL") and torch.cuda.is_available()),
                                 reason="needs the model (TTS_MODEL) and a GPU")]

PIECES_EN = ("ryan", ["Sure,", "tomorrow looks cloudy in the morning.", "Take an umbrella, just in case."])
PIECES_ZH = ("dylan", ["好的，", "明天上午多云，下午可能会下小雨。", "记得带伞。"])
OTHER = ("serena", "The kitchen lights are on, and the door is locked.")


@pytest.fixture(scope="module")
def engine():
    from app.tts_engine.engine import QwenTTSEngine

    e = QwenTTSEngine(os.environ["TTS_MODEL"], predictor="ours-fused", talker="ours-graph")
    e.load()
    return e


@pytest.fixture(scope="module")
def batch(engine):
    return engine.batch(slots=2)


@pytest.fixture(scope="module")
def text(engine):
    from app.work.reply_text import QwenReplyText

    return QwenReplyText(engine, lambda t: t, sample=False)   # greedy: runs repeat bit for bit


def speak(batch, text, voice, pieces, *, every=None, other=None, max_steps=3000):
    """A reply's frames: its first piece's prompt, then each later piece fed `every` steps after the
    last (None: all at once, before any step), then the end mark; `other` (voice, text): a sentence in
    the other row meanwhile. Returns (the reply's frames (n, 16), its trace of code-1 scores)."""
    trace, frames = [], []
    slot = batch.join(text.prompt(pieces[0], voice, None), trace=trace)
    said, rest = pieces[0], list(pieces[1:])
    if other is not None:
        batch.join(text.prompt(other[1], other[0], None))
        batch.finish(1 - slot, text.end())
    if every is None:
        for p in rest:
            batch.feed(slot, text.more(p, said), limit=2000)
            said += " " + p
        rest = []
        batch.finish(slot, text.end())
    steps_since = 0
    for _ in range(max_steps):
        if rest and steps_since >= every:                   # the agent's next piece, on its own schedule
            batch.feed(slot, text.more(rest[0], said), limit=2000)
            said += " " + rest.pop(0)
            steps_since = 0
            if not rest:
                batch.finish(slot, text.end())
        out, ended = batch.step()
        steps_since += 1
        if slot in out:
            frames.append(out[slot])
        if slot in ended:
            for b in range(batch.slots):                    # the other row, if still going
                if batch.state[b] is not None:
                    batch.leave(b)
            return torch.stack(frames).cpu(), trace
    raise AssertionError("the reply didn't end")


@pytest.mark.parametrize("reply", [PIECES_EN, PIECES_ZH])
def test_waiting_for_text_changes_nothing(batch, engine, text, reply):
    voice, pieces = reply
    whole, _ = speak(batch, text, voice, pieces)
    fed, _ = speak(batch, text, voice, pieces, every=3, other=OTHER)   # waits beside a busy row
    assert torch.equal(fed, whole)


@pytest.mark.parametrize("reply", [PIECES_EN, PIECES_ZH])
def test_it_is_qwens_streaming_mode(batch, engine, text, reply):
    """Qwen's exact talker, fed the same text (the prompt with its end mark, as Qwen builds it) and our
    row's frames: code 1's scores step by step, by the near-tie standard."""
    from app.tts_engine.talker import TalkerLoop
    from app.work.reply_text import joined
    from tests.test_tts_gpu import bf16_steps

    voice, pieces = reply
    whole = pieces[0]
    for p in pieces[1:]:
        whole += joined(whole, p)
    ours, got = speak(batch, text, voice, [whole])
    prompt = engine.prompt(whole, voice, sample=False, streaming=True)
    want = []
    with torch.inference_mode():
        TalkerLoop(engine._talker).generate(**prompt, predictor=None, emit=lambda codes: None,
                                            forced=[f[None] for f in ours.to("cuda")], trace=want)
    want, got = torch.cat(want), torch.cat(got)
    n = min(len(want), len(got))
    want, got = want[:n], got[:n]
    top2 = want.topk(2, dim=-1).values
    differs = want.argmax(-1) != got.argmax(-1)
    near_tie = (top2[:, 0] - top2[:, 1]) <= 2 * bf16_steps(top2[:, 0])
    assert not (differs & ~near_tie).any()
    assert differs.float().mean() < 0.02


def test_it_ends_by_itself_and_speaks_every_piece(batch, engine):
    """Ended by the model after the end mark (not the limit), with time for every piece. Sampled, as the
    service runs: greedy, some replies never end (2026-11-18: 1,994 frames, the cache's limit, which held)."""
    from app.work.reply_text import QwenReplyText

    voice, pieces = PIECES_EN
    frames, _ = speak(batch, QwenReplyText(engine, lambda t: t), voice, pieces, every=2)
    assert 40 < len(frames) < 300                          # ~7 s of speech, well under the limit
