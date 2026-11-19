"""Split a recording session into one clip per utterance.

    python segment.py data/datasets/full/recordings/hey-pi-session-1.wav data/datasets/processed/train/heypi-me-pi
    python segment.py TAKE.wav OUT --auto --max 2.5      # a mic with a noise floor (the Mac's); a longer word

The Pi's speakerphone gates its mic: between utterances the audio is (near) digital silence, so an
utterance is a run of 10 ms windows above -50 dBFS, closed by 250 ms of quiet. Each clip keeps 150 ms
before and after, is 16 kHz 16-bit mono (the training notebook's format), and is skipped if shorter
than 0.25 s (a click) or longer than `--max` seconds (1.5: the model's longest wake word, not a lone "Hey Pi").
`--auto`: a mic that isn't gated (the Mac's) has a steady noise floor, so "loud" is 12 dB over the take's own
floor (its 20th percentile) rather than -50 dBFS. Clips are named `{take}-{NNN}.wav`; `split()` is the same,
for other scripts (recording/label.py), with each clip's place in the take.
"""

import sys
import wave
from pathlib import Path

import numpy as np

WIN = 160                                                  # 10 ms at 16 kHz


def read(path: Path) -> np.ndarray:
    with wave.open(str(path)) as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1 and w.getsampwidth() == 2, "16 kHz 16-bit mono"
        return np.frombuffer(w.readframes(w.getnframes()), np.int16)


def split(x: np.ndarray, auto: bool = False, longest: float = 1.5, log=print) -> list[tuple[int, int]]:
    """The utterances in `x`: (first sample, end sample) of each clip, its 150 ms margins included."""
    n = len(x) // WIN
    db = 20 * np.log10(np.sqrt(np.mean((x[: n * WIN].astype(np.float32) / 32768).reshape(n, WIN) ** 2, axis=1)) + 1e-9)
    threshold = max(-50.0, float(np.percentile(db, 20)) + 12) if auto else -50.0
    loud = db > threshold
    log(f"loud: over {threshold:.0f} dBFS")
    runs, start, quiet = [], None, 0
    for i, v in enumerate(loud):
        if v:
            start = i if start is None else start
            quiet = 0
        elif start is not None:
            quiet += 1
            if quiet >= 25:                               # 250 ms of quiet: the utterance ended
                runs.append((start, i - quiet + 1))
                start, quiet = None, 0
    if start is not None:
        runs.append((start, n))
    clips = []
    for a, b in runs:
        seconds = (b - a) * WIN / 16000
        if not 0.25 <= seconds <= longest:
            log(f"  skipped {a * 0.01:6.2f} s: {seconds:.2f} s long")
            continue
        clips.append((max(0, (a - 15) * WIN), min(len(x), (b + 15) * WIN)))
    return clips


def save(path: Path, pcm: np.ndarray) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(16000), w.writeframes(pcm.tobytes())


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    longest = 1.5
    if "--max" in sys.argv:
        longest = float(sys.argv[sys.argv.index("--max") + 1])
        args.remove(sys.argv[sys.argv.index("--max") + 1])
    src, out = Path(args[0]), Path(args[1])
    out.mkdir(parents=True, exist_ok=True)
    x = read(src)
    clips = split(x, auto="--auto" in sys.argv, longest=longest)
    for k, (lo, hi) in enumerate(clips, 1):
        save(out / f"{src.stem}-{k:03d}.wav", x[lo:hi])
    print(f"{len(clips)} clips → {out}")
