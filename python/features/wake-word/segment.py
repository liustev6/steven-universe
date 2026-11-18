"""Split a recording session of the wake word into one clip per utterance.

    python segment.py data/hey-pi-session-1.wav data/positives/me

The Pi's speakerphone gates its mic: between utterances the audio is (near) digital silence, so an
utterance is a run of 10 ms windows above -50 dBFS, closed by 250 ms of quiet. Each clip keeps 150 ms
before and after, is 16 kHz 16-bit mono (the training notebook's format), and is skipped if shorter
than 0.25 s (a click) or longer than 1.5 s (the model's longest wake word: not a lone "Hey Pi").
"""

import sys
import wave
from pathlib import Path

import numpy as np

src, out = Path(sys.argv[1]), Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
with wave.open(str(src)) as w:
    assert w.getframerate() == 16000 and w.getnchannels() == 1 and w.getsampwidth() == 2, "16 kHz 16-bit mono"
    x = np.frombuffer(w.readframes(w.getnframes()), np.int16)
win = 160
n = len(x) // win
db = 20 * np.log10(np.sqrt(np.mean((x[: n * win].astype(np.float32) / 32768).reshape(n, win) ** 2, axis=1)) + 1e-9)
loud = db > -50

clips, start, quiet = [], None, 0
for i, v in enumerate(loud):
    if v:
        start = i if start is None else start
        quiet = 0
    elif start is not None:
        quiet += 1
        if quiet >= 25:                                   # 250 ms of quiet: the utterance ended
            clips.append((start, i - quiet + 1))
            start, quiet = None, 0
if start is not None:
    clips.append((start, n))

kept = skipped = 0
for a, b in clips:
    seconds = (b - a) * win / 16000
    if not 0.25 <= seconds <= 1.5:
        skipped += 1
        print(f"  skipped {a * 0.01:6.2f} s: {seconds:.2f} s long")
        continue
    lo, hi = max(0, (a - 15) * win), min(len(x), (b + 15) * win)
    kept += 1
    with wave.open(str(out / f"{src.stem}-{kept:03d}.wav"), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(16000), w.writeframes(x[lo:hi].tobytes())
print(f"{kept} clips → {out} ({skipped} skipped)")
