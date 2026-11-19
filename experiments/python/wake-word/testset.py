"""The wake-word test set: fixed, rendered once, never trained on (docs/specs/wake-word.md, W7). Every model is scored
on exactly these files (evaluate.py). As data grows, sources are added and a new version is built as a superset: the
same rule renders an existing clip the same way, so v1's clips are in v2 unchanged. Nothing listed here may ever be
used for training or validation.

    python testset.py <data root>                 # → <data root>/testset/v1/: audio/ and manifest.jsonl

Each synthetic clip (a voice no model trained on) gets one listening condition, fixed by its name, in the share a home
has; its background comes only from test-only sound:

    quiet                   40%   no background
    room noise              25%   the owner's room on the Pi's mic (its held-out minutes), +15 to +20 dB
    video, TV or meeting    25%   the owner's held-out short videos or AliMeeting's eval meetings, +5 to +10 dB
    very noisy              10%   the same, 0 to +5 dB

The owner's own clips stay as recorded (the Pi's mic, quiet or with a video playing; the Mac's): real conditions
already. Near misses get the same mix as the word. The false-wake streams (evaluate.py) are test-only too.
"""

import json
import shutil
import sys
import wave
import zlib
from pathlib import Path

import numpy as np

ROOT = Path(sys.argv[1])
EVAL = ROOT / "xiao_pai/eval"
VERSION = "v1"
OUT = ROOT / "testset" / VERSION

CONDITIONS = [  # (name, share in %, background pool, dB range: the voice over the background)
    ("quiet", 40, None, None),
    ("room noise", 25, "room", (15, 20)),
    ("video, TV or meeting", 25, "media", (5, 10)),
    ("very noisy", 10, "media", (0, 5)),
]
POOLS = {
    "room": [sorted((EVAL / "me-pi-room").glob("*.wav"))],
    "media": [sorted((EVAL / "video").glob("*.wav")), sorted((ROOT / "datasets/alimeeting/eval_far_ch0").glob("*.wav"))],
}
# (word, kind, set name, folder, pattern, synthetic: mixed by condition; else the owner's, as recorded)
SOURCES = [
    ("小派小派", "positive", "51 unheard voices, designed", EVAL / "tts3-positive", "d*.wav", True),
    ("小派小派", "positive", "51 unheard voices, cloned", EVAL / "tts3-positive", "c*.wav", True),
    ("小派小派", "positive", "spike 01's 2 unheard voices", EVAL / "positive", "*.wav", True),
    ("小派小派", "positive", "the owner, Pi mic, quiet", EVAL / "me-pi-positive", "pi-wake-2*.wav", False),
    ("小派小派", "positive", "the owner, Pi mic, a video playing", EVAL / "me-pi-positive", "pi-wake-bg-*.wav", False),
    ("小派小派", "positive", "the owner, Mac mic", EVAL / "me-positive", "*.wav", False),
    ("小派小派", "near", "51 voices, core 30 (小胖小胖, 小怕小怕 …)", EVAL / "tts3-near", "*.wav", True),
    ("小派小派", "near", "51 voices, doubled 25 (小快小快, 小派小心 …)", EVAL / "tts4-doubled", "*.wav", True),
    ("小派小派", "near", "spike 01's near misses", EVAL / "near_miss", "*.wav", True),
    ("小派小派", "near", "the owner's near misses, Pi mic", EVAL / "me-pi-near", "*.wav", False),
    ("小派小派", "near", "the owner's other lines, Mac mic", EVAL / "me-negative", "*.wav", False),
    ("Hey Pi", "positive", "51 unheard voices, designed", EVAL / "heypi-positive", "d*.wav", True),
    ("Hey Pi", "positive", "51 unheard voices, cloned", EVAL / "heypi-positive", "c*.wav", True),
    ("Hey Pi", "near", "51 voices, Hey Pi's 18 (Hey pal, Apple pie …)", EVAL / "heypi-near", "*.wav", True),
]


def read(path: Path) -> np.ndarray:
    with wave.open(str(path)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32)


def condition(key: str):
    h = zlib.crc32(key.encode()) % 100
    for name, share, pool, snr in CONDITIONS:
        if h < share:
            return name, pool, snr
        h -= share


def background(pool: str, key: str, length: int) -> tuple[np.ndarray, str, int]:
    """A stretch of the pool's sound for this clip, from its name: not near-silent (a pause in a meeting)."""
    h = zlib.crc32(("bg" + key).encode())
    groups = POOLS[pool]
    files = groups[h % len(groups)]
    f = files[(h // 7) % len(files)]
    with wave.open(str(f)) as w:
        total = w.getnframes()
        for k in range(30):
            offset = (h // 13 + k * 48_000) % max(1, total - length)
            w.setpos(offset)
            n = np.frombuffer(w.readframes(length), np.int16).astype(np.float32)
            if len(n) and 20 * np.log10(np.sqrt((n ** 2).mean()) / 32768 + 1e-9) > -45:
                break
    return np.tile(n, length // max(1, len(n)) + 1)[:length], f.name, offset


def main():
    shutil.rmtree(OUT, ignore_errors=True)
    (OUT / "audio").mkdir(parents=True)
    rows, counts = [], {}
    for word, kind, set_name, folder, pattern, synthetic in SOURCES:
        for src in sorted(folder.glob(pattern)):
            key = f"{word}/{kind}/{src.name}"
            x = read(src)
            row = {"word": word, "kind": kind, "set": set_name, "source": str(src.relative_to(ROOT))}
            if synthetic:
                cond, pool, snr = condition(key)
                if pool:
                    n, noise, offset = background(pool, key, len(x))
                    db = snr[0] + (zlib.crc32(("snr" + key).encode()) % 1000) / 1000 * (snr[1] - snr[0])
                    x = x + np.sqrt((x ** 2).mean() / max((n ** 2).mean(), 1e-9) / 10 ** (db / 10)) * n
                    row |= {"noise": noise, "offset": offset, "snr_db": round(db, 2)}
                row["condition"] = cond
            else:
                row["condition"] = "as recorded"
            clip = f"{len(rows):05d}.wav"
            with wave.open(str(OUT / "audio" / clip), "wb") as w:
                w.setnchannels(1), w.setsampwidth(2), w.setframerate(16000)
                w.writeframes(np.clip(x, -32768, 32767).astype(np.int16).tobytes())
            rows.append({"clip": clip} | row)
            c = (word, kind, row["condition"])
            counts[c] = counts.get(c, 0) + 1
    with open(OUT / "manifest.jsonl", "w") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"test set {VERSION}: {len(rows)} clips → {OUT}")
    for (word, kind, cond), n in sorted(counts.items()):
        print(f"  {word:6s} {kind:8s} {cond:22s} {n:5d}")


if __name__ == "__main__":
    main()
