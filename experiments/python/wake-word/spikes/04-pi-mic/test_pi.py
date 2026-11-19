"""Spike 04: a wake-word model on the owner's Pi-mic recordings (recording/pi.html), scored as pi-server runs it
(spike 03's evaluate.py functions). With any venv that has pymicro-wakeword (pi-server's, on the Mac):

    python spikes/04-pi-mic/test_pi.py out/xiao_pai_03.json [--held-out]

Without --held-out, every Pi take (for a model that trained on none of them: spike 03's); with it, only the sets
sort_recordings.py held out (for a model trained on the rest). Prints each false wake in the room with its time.
"""

import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parents[2]
EVAL = HERE / "spikes/03-many-voices/evaluate.py"
held = "--held-out" in sys.argv
model = next(a for a in sys.argv[1:] if not a.startswith("--"))
sys.argv = [str(EVAL), model, str(HERE / "data/xiao_pai")]
ns = {"__file__": str(EVAL)}
exec(EVAL.read_text().split("rng = random.Random(1)")[0], ns)        # its functions, not its tests
peak, read, probabilities, wakes, CUTOFFS = ns["peak"], ns["read"], ns["probabilities"], ns["wakes"], ns["CUTOFFS"]

DATA = HERE / "data/xiao_pai"
REC = DATA / "recordings"
rows = [json.loads(line) for line in (REC / "transcripts.jsonl").read_text().splitlines() if line.strip()]
clips = {k: [r for r in rows if r["take"].startswith("pi-") and r["kind"] == k] for k in ("wake", "wake-bg", "near")}
if held:
    keep = {p.name for d in ("eval/me-pi-positive", "eval/me-pi-near") for p in (DATA / d).glob("*.wav")}
    clips = {k: [r for r in v if Path(r["clip"]).name in keep] for k, v in clips.items()}
else:
    clips["wake-bg"] = [r for r in clips["wake-bg"] if r["asr"] and "小" in r["asr"]]


def line(name: str, values) -> None:
    v = np.array(values)
    print(f"  {name:34s}" + "".join(f"{np.mean(v >= c):>8.0%}" for c in CUTOFFS))


print(f"{Path(model).stem} on the Pi's mic ({'held-out sets' if held else 'every take'})")
print(f"\nWAKE WORD: share woken\n  {'':34s}" + "".join(f"{c:>8.2f}" for c in CUTOFFS))
scores = {k: {r["clip"]: peak(read(REC / r["clip"])) for r in v} for k, v in clips.items()}
line(f"quiet room ({len(scores['wake'])})", list(scores["wake"].values()))
line(f"a video playing ({len(scores['wake-bg'])})", list(scores["wake-bg"].values()))

print(f"\nNEAR MISSES: share that wakes it\n  {'':34s}" + "".join(f"{c:>8.2f}" for c in CUTOFFS))
line(f"all ({len(scores['near'])})", list(scores["near"].values()))
by = {}
for r in clips["near"]:
    by.setdefault(r["text"], []).append(scores["near"][r["clip"]])
for text, v in sorted(by.items(), key=lambda kv: -max(kv[1]))[:8]:
    print(f"    {text:20s} peaks {', '.join(f'{x:.2f}' for x in v)}")

files = sorted((DATA / "eval/me-pi-room").glob("*.wav")) if held else sorted(REC.glob("pi-room-*.wav"))
if files:
    x = np.concatenate([read(f) for f in files])
    p, hours = probabilities(x), len(x) / 16000 / 3600
    print(f"\nTHE ROOM ({hours * 60:.0f} min, one stream): false wakes per hour\n  {'':34s}" + "".join(f"{c:>8.2f}" for c in CUTOFFS))
    print(f"  {'':34s}" + "".join(f"{wakes(p, c) / hours:>8.1f}" for c in CUTOFFS))
    step = len(x) / len(p) / 16000                                        # seconds per probability
    i, low = 0, min(CUTOFFS[1:])
    while i < len(p):                                                     # each wake at the lowest cutoff but 0.80
        if p[i] >= low:
            j = i + int(np.argmax(p[i:i + 100]))
            t = j * step
            print(f"    {int(t // 60):2d}:{t % 60:04.1f}  peak {p[j]:.2f}  ({files[int(t // 300)].name if not held else ''})")
            i += 100
        else:
            i += 1
