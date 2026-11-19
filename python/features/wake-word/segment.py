"""Split a recording session of the wake word into one clip per utterance.

    python segment.py data/hey-pi-session-1.wav data/positives/me
    python segment.py TAKE.wav OUT --auto --max 2.5      # a mic with a noise floor (the Mac's); a longer word

The Pi's speakerphone gates its mic: between utterances the audio is (near) digital silence, so an
utterance is a run of 10 ms windows above -50 dBFS, closed by 250 ms of quiet. Each clip keeps 150 ms
before and after, is 16 kHz 16-bit mono (the training notebook's format), and is skipped if shorter
than 0.25 s (a click) or longer than `--max` seconds (1.5: the model's longest wake word, not a lone "Hey Pi").
`--auto`: a mic that isn't gated (the Mac's) has a steady noise floor, so "loud" is 12 dB over the take's own
floor (its 20th percentile) rather than -50 dBFS.
"""

import sys
import wave
from pathlib import Path

import numpy as np

args = [a for a in sys.argv[1:] if not a.startswith("--")]
src, out = Path(args[0]), Path(args[1])
auto = "--auto" in sys.argv
longest = float(sys.argv[sys.argv.index("--max") + 1]) if "--max" in sys.argv else 1.5
if "--max" in sys.argv:
    args.remove(sys.argv[sys.argv.index("--max") + 1])
out.mkdir(parents=True, exist_ok=True)
with wave.open(str(src)) as w:
    assert w.getframerate() == 16000 and w.getnchannels() == 1 and w.getsampwidth() == 2, "16 kHz 16-bit mono"
    x = np.frombuffer(w.readframes(w.getnframes()), np.int16)
win = 160
n = len(x) // win
db = 20 * np.log10(np.sqrt(np.mean((x[: n * win].astype(np.float32) / 32768).reshape(n, win) ** 2, axis=1)) + 1e-9)
threshold = max(-50.0, float(np.percentile(db, 20)) + 12) if auto else -50.0
loud = db > threshold
print(f"loud: over {threshold:.0f} dBFS")

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
    if not 0.25 <= seconds <= longest:
        skipped += 1
        print(f"  skipped {a * 0.01:6.2f} s: {seconds:.2f} s long")
        continue
    lo, hi = max(0, (a - 15) * win), min(len(x), (b + 15) * win)
    kept += 1
    with wave.open(str(out / f"{src.stem}-{kept:03d}.wav"), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(16000), w.writeframes(x[lo:hi].tobytes())
print(f"{kept} clips → {out} ({skipped} skipped)")
