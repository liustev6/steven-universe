"""Spike 01: how well a wake-word model does on audio it never trained on, scored as pi-server runs it
(pymicro-wakeword, streaming, its 5-frame average). Run with pi-server's venv:

    ../../services/pi-server/.venv/bin/python spikes/01-xiao-pai/evaluate.py out/xiao_pai.json data/xiao_pai

  recall       the held-out voices (never in training) saying the word: clean, and mixed with background
               sound (AudioSet clips) at 10 and 5 dB SNR, as a room would
  near misses  the held-out voices' near misses: the share that wakes it
  speech/h     everyday Chinese from the held-out voices, your "Hey Pi" recordings (the Pi's mic), and (spike 02,
               when there) 2 h of AliMeeting's test meetings, each played as one stream: false wakes per hour
               (a wake, then 1 s before another counts)
"""

import collections
import random
import sys
import wave
from pathlib import Path

import numpy as np
from pymicro_wakeword import MicroWakeWord, MicroWakeWordFeatures

MODEL, DATA = Path(sys.argv[1]), Path(sys.argv[2])
HERE = Path(__file__).resolve().parents[2]
CUTOFFS = (0.80, 0.90, 0.95, 0.97, 0.99)
STEP = 160                                            # 10 ms at 16 kHz


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


def pad(x: np.ndarray) -> np.ndarray:
    z = np.zeros(16000, np.float32)
    return np.concatenate([z, x, z])


def mix(x: np.ndarray, noise: np.ndarray, snr_db: float) -> np.ndarray:
    if len(noise) < len(x):
        noise = np.tile(noise, len(x) // len(noise) + 1)
    n = noise[:len(x)]
    gain = np.sqrt((x ** 2).mean() / max((n ** 2).mean(), 1e-9) / 10 ** (snr_db / 10))
    return x + gain * n


def wakes(p: np.ndarray, cutoff: float) -> int:
    """Detections in a stream: above the cutoff, then 1 s (100 steps) before the next can count."""
    n, i = 0, 0
    while i < len(p):
        if p[i] >= cutoff:
            n, i = n + 1, i + 100
        else:
            i += 1
    return n


def table(title: str, rows: list[tuple[str, list[str]]]) -> None:
    print(f"\n{title}\n  {'':28s}" + "".join(f"{c:>8.2f}" for c in CUTOFFS))
    for name, cells in rows:
        print(f"  {name:28s}" + "".join(f"{c:>8s}" for c in cells))


rng = random.Random(1)
noise = [read(p) for p in sorted((HERE / "work/audioset_16k").glob("*.wav"))[:200]]

# ---- recall: the held-out voices --------------------------------------------------------------------------
positives = sorted((DATA / "eval/positive").glob("*.wav"))
peaks = {"clean": [], "10 dB": [], "5 dB": []}
by_voice = collections.defaultdict(list)
for f in positives:
    x = read(f)
    peaks["clean"].append(probabilities(pad(x)).max())
    by_voice[f.name.split("-")[0]].append(peaks["clean"][-1])
    for snr in (10, 5):
        peaks[f"{snr} dB"].append(probabilities(pad(mix(x, rng.choice(noise), snr))).max())
table(f"RECALL: held-out voices saying 小派小派 ({len(positives)} clips), share woken, by cutoff",
      [(k, [f"{np.mean(np.array(v) >= c):.0%}" for c in CUTOFFS]) for k, v in peaks.items()]
      + [(f"  clean, {voice}", [f"{np.mean(np.array(v) >= c):.0%}" for c in CUTOFFS]) for voice, v in sorted(by_voice.items())])

# ---- near misses ---------------------------------------------------------------------------------------------
near = sorted((DATA / "eval/near_miss").glob("*.wav"))
if near:
    import yaml
    phrases = yaml.safe_load(open(HERE / "phrases/xiao_pai.yaml"))["near_miss"]
    by_phrase = collections.defaultdict(list)
    for f in near:
        by_phrase[phrases[int(f.name.split("-")[1])]].append(probabilities(pad(read(f))).max())
    all_near = [p for v in by_phrase.values() for p in v]
    worst = sorted(by_phrase.items(), key=lambda kv: -np.mean(kv[1]))[:6]
    table(f"NEAR MISSES: held-out voices ({len(near)} clips), share that wakes it",
          [("all", [f"{np.mean(np.array(all_near) >= c):.0%}" for c in CUTOFFS])]
          + [(f"  {k}", [f"{np.mean(np.array(v) >= c):.0%}" for c in CUTOFFS]) for k, v in worst])

# ---- false wakes per hour, as streams ----------------------------------------------------------------------
streams = {"Chinese speech (held-out)": sorted((DATA / "eval/speech").glob("*.wav")),
           "your 'Hey Pi' recordings": sorted((HERE / "data/positives/me").glob("*.wav")),
           "AudioSet sounds (200 clips)": sorted((HERE / "work/audioset_16k").glob("*.wav"))[200:400]}
# spike 02: AliMeeting's test meetings (never trained on), one channel of the far-field array, 2 h of them
meetings = sorted((HERE / "data/datasets/alimeeting/test_far_ch0").glob("*.wav"))
if meetings:
    streams["Mandarin meetings, far-field"] = meetings
rows = []
for name, files in streams.items():
    if not files:
        continue
    x = np.concatenate([np.concatenate([read(f), np.zeros(8000, np.float32)]) for f in files])
    x = x[:16000 * 3600 * 2]                                     # at most 2 h of any one set
    p, hours = probabilities(x), len(x) / 16000 / 3600
    rows.append((f"{name} ({hours * 60:.0f} min)", [f"{wakes(p, c) / hours:.1f}" for c in CUTOFFS]))
table("FALSE WAKES PER HOUR, as one continuous stream", rows)
