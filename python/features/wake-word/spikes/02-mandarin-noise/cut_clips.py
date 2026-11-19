"""Spike 02: long recordings → short negative clips for train.py (16 kHz mono 16-bit WAVs), up to a cap of
hours, taken evenly across every recording (every k-th clip of each), near-silence skipped.

    .venv/bin/python spikes/02-mandarin-noise/cut_clips.py 'data/datasets/alimeeting/train_far_ch0/*.wav' \
        data/xiao_pai_02/negatives/alimeeting --seconds 3 --hours 40
"""

import argparse
import glob
import math
from pathlib import Path

import numpy as np
import soundfile

ap = argparse.ArgumentParser()
ap.add_argument("pattern")
ap.add_argument("out")
ap.add_argument("--seconds", type=float, default=3.0)
ap.add_argument("--hours", type=float, default=40.0)
ap.add_argument("--quiet-db", type=float, default=-50.0, help="clips quieter than this (RMS, dBFS) are skipped")
args = ap.parse_args()

files = sorted(glob.glob(args.pattern))
n = int(16000 * args.seconds)
total = sum(soundfile.info(f).frames // n for f in files)
keep_every = max(1, math.ceil(total * args.seconds / 3600 / args.hours))
out = Path(args.out)
out.mkdir(parents=True, exist_ok=True)
kept = skipped = 0
for f in files:
    x, sr = soundfile.read(f, dtype="int16")
    assert sr == 16000 and x.ndim == 1, (f, sr, x.shape)
    for i in range(0, len(x) // n, keep_every):
        clip = x[i * n:(i + 1) * n]
        rms = np.sqrt(np.mean(clip.astype(np.float32) ** 2)) / 32768
        if 20 * np.log10(max(rms, 1e-9)) < args.quiet_db:
            skipped += 1
            continue
        soundfile.write(out / f"{Path(f).parent.name}-{Path(f).stem}-{i:05d}.wav", clip, sr, subtype="PCM_16")
        kept += 1
print(f"{out}: {kept} clips ({kept * args.seconds / 3600:.1f} h; every {keep_every} of {total}; {skipped} near-silent skipped)")
