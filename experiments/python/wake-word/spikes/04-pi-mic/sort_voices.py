"""Spike 04: the synthetic voices' near_miss_doubled clips (gen_voices.py --lines doubled --out spike-04) into the
sets train.py and evaluate.py read. The voices spike 03 held out (those in eval/tts3-positive) stay held out.

    python spikes/04-pi-mic/sort_voices.py /srv/experiments/wake-word/out/spike-04 /srv/experiments/wake-word/data/xiao_pai
"""

import shutil
import sys
from pathlib import Path

SRC, DATA = Path(sys.argv[1]), Path(sys.argv[2])
held = {p.name.split("-")[0] for p in (DATA / "eval/tts3-positive").glob("*.wav")}
train, test = DATA / "negatives/tts4-doubled", DATA / "eval/tts4-doubled"
for d in (train, test):
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
n = {train: 0, test: 0}
for voice in sorted(p for p in SRC.iterdir() if (p / "done").exists()):
    dest = test if voice.name in held else train
    for f in voice.glob("*.wav"):
        shutil.copy2(f, dest / f.name)
        n[dest] += 1
print(f"{n[train]} clips → {train}\n{n[test]} clips → {test} ({len(held)} held-out voices)")
