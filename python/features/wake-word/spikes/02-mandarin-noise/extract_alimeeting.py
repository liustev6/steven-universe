"""Spike 02: one channel of AliMeeting's far-field array, from the archive as it streams (no 73 GB unpack):
each meeting's 8-channel recording → its first channel, 16 kHz mono 16-bit, one WAV per meeting.

    .venv/bin/python spikes/02-mandarin-noise/extract_alimeeting.py data/datasets/alimeeting/Train_Ali_far.tar.gz \
        data/datasets/alimeeting/train_far_ch0
"""

import io
import sys
import tarfile
from pathlib import Path

import numpy as np
import soundfile

archive, out = Path(sys.argv[1]), Path(sys.argv[2])
only = sys.argv[3] if len(sys.argv) > 3 else ""             # e.g. "_far/": the eval/test archives hold both
out.mkdir(parents=True, exist_ok=True)
n, hours = 0, 0.0
with tarfile.open(archive, "r|gz") as tar:                     # a stream: one pass, never seeks
    for member in tar:
        if not member.isfile() or not member.name.endswith(".wav") or only not in member.name:
            continue
        target = out / Path(member.name).name
        data = tar.extractfile(member).read()
        if target.exists():
            continue                                            # a rerun: already done
        x, sr = soundfile.read(io.BytesIO(data), dtype="int16", always_2d=True)
        assert sr == 16000, (member.name, sr)
        soundfile.write(target, np.ascontiguousarray(x[:, 0]), sr, subtype="PCM_16")
        n, hours = n + 1, hours + len(x) / sr / 3600
        print(f"{n:4d} {target.name}  {x.shape[1]} ch → 1, {len(x) / sr / 60:.0f} min  (total {hours:.1f} h)", flush=True)
print(f"done: {n} meetings, {hours:.1f} h")
