"""Spike 03: a wake-word model on audio it never trained on, scored as pi-server runs it (pymicro-wakeword,
streaming, its 5-frame average). Spike 01's tests plus the new held-out sets. With pi-server's venv (the Mac) or
any venv with pymicro-wakeword:

    python spikes/03-many-voices/evaluate.py out/xiao_pai_03.json <data root>/xiao_pai      (WORK: the trainer's cache)

  recall        unheard voices saying the word, clean and mixed with background sound at 10 and 5 dB: spike 01's
                two (serena, uncle_fu), the 51 held-out designed and cloned voices, and your own 7 held-out takes
  near misses   the 51 held-out voices' core near misses (by phrase), spike 01's, and your own held-out lines
  false wakes   per hour, each set as one stream (a wake, then 1 s before another counts): your short videos (15 min
                held out), Mandarin meetings (AliMeeting's test set, 2 h), everyday Chinese from unheard voices, your
                "Hey Pi" recordings, AudioSet sounds
"""

import collections
import os
import random
import sys
import wave
from pathlib import Path

import numpy as np
import yaml
from pymicro_wakeword import MicroWakeWord, MicroWakeWordFeatures

MODEL, DATA = Path(sys.argv[1]), Path(sys.argv[2])
ROOT = DATA.parent
HERE = Path(__file__).resolve().parents[2]
WORK = Path(os.environ.get("WORK", HERE / "work"))
CUTOFFS = (0.80, 0.90, 0.95, 0.97, 0.99)
STEP = 160                                            # 10 ms at 16 kHz
BANK = yaml.safe_load(open(HERE / "phrases/xiao_pai_bank.yaml"))
POSITIVES = 14                                        # gen_voices.py: each voice's first 14 lines are the wake word


def read(path: Path) -> np.ndarray:
    with wave.open(str(path)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32)


def probabilities(x: np.ndarray) -> np.ndarray:
    """The model's 5-frame average after each step, streaming, from a fresh state."""
    model, features = MicroWakeWord.from_config(MODEL), MicroWakeWordFeatures()
    window = collections.deque(maxlen=model.sliding_window_size)
    out = []
    pcm = np.clip(x, -32768, 32767).astype(np.int16).tobytes()
    for i in range(0, len(pcm) - 2 * STEP + 1, 2 * STEP):
        for f in features.process_streaming(pcm[i:i + 2 * STEP]):
            model.process_streaming(f)
            p = list(model._probabilities)
            if len(p) >= model.sliding_window_size:
                out.append(sum(p) / len(p))
    return np.array(out or [0.0])


def peak(x: np.ndarray) -> float:
    z = np.zeros(16000, np.float32)
    return float(probabilities(np.concatenate([z, x, z])).max())


def mix(x: np.ndarray, noise: np.ndarray, snr_db: float) -> np.ndarray:
    if len(noise) < len(x):
        noise = np.tile(noise, len(x) // len(noise) + 1)
    n = noise[:len(x)]
    return x + np.sqrt((x ** 2).mean() / max((n ** 2).mean(), 1e-9) / 10 ** (snr_db / 10)) * n


def wakes(p: np.ndarray, cutoff: float) -> int:
    n, i = 0, 0
    while i < len(p):
        if p[i] >= cutoff:
            n, i = n + 1, i + 100
        else:
            i += 1
    return n


def share(values) -> list[str]:
    v = np.array(values)
    return [f"{np.mean(v >= c):.0%}" for c in CUTOFFS]


def table(title: str, rows: list[tuple[str, list[str]]]) -> None:
    print(f"\n{title}\n  {'':34s}" + "".join(f"{c:>8.2f}" for c in CUTOFFS))
    for name, cells in rows:
        print(f"  {name:34s}" + "".join(f"{c:>8s}" for c in cells))


rng = random.Random(1)
noise = [read(p) for p in sorted((WORK / "audioset_16k").glob("*.wav"))[:200]]

# ---- recall ---------------------------------------------------------------------------------------------------
sets = {"serena, uncle_fu (spike 01)": sorted((DATA / "eval/positive").glob("*.wav")),
        "51 new voices: designed": sorted((DATA / "eval/tts3-positive").glob("d*.wav")),
        "51 new voices: cloned": sorted((DATA / "eval/tts3-positive").glob("c*.wav")),
        "your voice (7 held out)": sorted((DATA / "eval/me-positive").glob("*.wav"))}
rows = []
for name, files in sets.items():
    if not files:
        continue
    clean, n10, n5 = [], [], []
    for f in files:
        x = read(f)
        clean.append(peak(x))
        n10.append(peak(mix(x, rng.choice(noise), 10)))
        n5.append(peak(mix(x, rng.choice(noise), 5)))
    rows += [(f"{name} ({len(files)})", share(clean)), ("  + background 10 dB", share(n10)), ("  + background 5 dB", share(n5))]
table("RECALL: unheard voices saying 小派小派, share woken, by cutoff", rows)

# ---- near misses and your other speech ------------------------------------------------------------------------
rows = []
near = sorted((DATA / "eval/tts3-near").glob("*.wav"))
by_phrase = collections.defaultdict(list)
for f in near:
    k = int(f.stem.rsplit("-", 1)[1])
    by_phrase[BANK["near_miss_core"][k - POSITIVES]].append(peak(read(f)))
if near:
    rows.append((f"51 new voices, core ({len(near)})", share([p for v in by_phrase.values() for p in v])))
    rows += [(f"  {k}", share(v)) for k, v in sorted(by_phrase.items(), key=lambda kv: -np.mean(kv[1]))[:8]]
old = sorted((DATA / "eval/near_miss").glob("*.wav"))
if old:
    rows.append((f"spike 01's near misses ({len(old)})", share([peak(read(f)) for f in old])))
mine = sorted((DATA / "eval/me-negative").glob("*.wav"))
if mine:
    rows.append((f"your own other lines ({len(mine)})", share([peak(read(f)) for f in mine])))
table("NEAR MISSES AND OTHER SPEECH: share that wakes it", rows)

# ---- false wakes per hour, as streams ---------------------------------------------------------------------------
streams = {"your short videos (held out)": sorted((DATA / "eval/video").glob("*.wav")),
           "Mandarin meetings (AliMeeting test)": sorted((ROOT / "datasets/alimeeting/test_far_ch0").glob("*.wav")),
           "everyday Chinese, unheard voices": sorted((DATA / "eval/speech").glob("*.wav"))
                                               + sorted((DATA / "eval/tts3-everyday").glob("*.wav")),
           "your 'Hey Pi' recordings": sorted((ROOT / "positives/me").glob("*.wav")),
           "AudioSet sounds (200 clips)": sorted((WORK / "audioset_16k").glob("*.wav"))[200:400]}
rows = []
for name, files in streams.items():
    if not files:
        continue
    x = np.concatenate([np.concatenate([read(f), np.zeros(8000, np.float32)]) for f in files])[:16000 * 3600 * 2]
    p, hours = probabilities(x), len(x) / 16000 / 3600
    rows.append((f"{name} ({hours * 60:.0f} min)", [f"{wakes(p, c) / hours:.1f}" for c in CUTOFFS]))
table("FALSE WAKES PER HOUR, as one continuous stream", rows)
