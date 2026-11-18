"""Why does a streamed reply fed with waits differ from the same reply fed at once? (test_stream_gpu)

Greedy, the same pieces and tokens each time; only the schedule changes:
    whole          all the text fed before the first step, alone in the batch
    waits          pieces fed every 3 steps (the row waits between), alone
    other          all fed at once, with a sentence in the other row
    waits+other    both (the failing test)
For each against `whole`: the first frame that differs, how many differ, and for the first difference
the top-2 margin of code 1 there (a near-tie, or not), plus how many times the row had waited before it.
"""

import sys

import torch

from app.tts_engine.engine import QwenTTSEngine
from app.work.reply_text import QwenReplyText

MODEL = sys.argv[1]
engine = QwenTTSEngine(MODEL, predictor="ours-fused", talker="ours-graph")
engine.load()
batch = engine.batch(slots=2)
text = QwenReplyText(engine, lambda t: t)
REPLIES = {"en": ("ryan", ["Sure,", "tomorrow looks cloudy in the morning.", "Take an umbrella, just in case."]),
           "zh": ("dylan", ["好的，", "明天上午多云，下午可能会下小雨。", "记得带伞。"])}
OTHER = ("serena", "The kitchen lights are on, and the door is locked.")


def speak(voice, pieces, *, every=None, other=False):
    trace, frames, waits_before = [], [], []
    slot = batch.join(text.prompt(pieces[0], voice, None), trace=trace)
    if other:
        o = batch.join(text.prompt(OTHER[1], OTHER[0], None))
        batch.finish(o, text.end())
    said, rest, waited, since = pieces[0], list(pieces[1:]), 0, 0
    if every is None:
        for p in rest:
            batch.feed(slot, text.more(p, said), limit=2000)
            said += " " + p
        rest = []
        batch.finish(slot, text.end())
    for _ in range(3000):
        if rest and since >= every:
            batch.feed(slot, text.more(rest[0], said), limit=2000)
            said += " " + rest.pop(0)
            since = 0
            if not rest:
                batch.finish(slot, text.end())
        if batch.state[slot] is not None and batch.state[slot].waiting():
            waited += 1
        out, ended = batch.step()
        since += 1
        if slot in out:
            frames.append(out[slot])
            waits_before.append(waited)
        if slot in ended:
            for b in range(batch.slots):
                if batch.state[b] is not None:
                    batch.leave(b)
            return torch.stack(frames).cpu(), torch.cat(trace).cpu(), waits_before
    raise AssertionError("no end")


for name, (voice, pieces) in REPLIES.items():
    whole, wt, _ = speak(voice, pieces)
    print(f"== {name}: whole {len(whole)} frames")
    for label, kw in (("waits", dict(every=3)), ("other", dict(other=True)), ("waits+other", dict(every=3, other=True)),
                      ("whole again", {})):
        got, gt, wb = speak(voice, pieces, **kw)
        n = min(len(got), len(whole))
        diff = (got[:n] != whole[:n]).any(-1).nonzero().flatten().tolist()
        if not diff and len(got) == len(whole):
            print(f"  {label:12} identical ({len(got)} frames)")
            continue
        f = diff[0] if diff else n
        top2 = wt[f].topk(2).values if f < len(wt) else None
        margin = f"{float(top2[0] - top2[1]):.4f} (top {float(top2[0]):.2f})" if top2 is not None else "?"
        col = (got[f] != whole[f]).nonzero().flatten().tolist() if f < n else []
        print(f"  {label:12} differ from frame {f} ({len(diff)} of {n}; lengths {len(got)} vs {len(whole)}); "
              f"codes differing there {col[:6]}; code-1 margin in whole's trace {margin}; the row had waited "
              f"{wb[f] if f < len(wb) else '?'} steps before it")
