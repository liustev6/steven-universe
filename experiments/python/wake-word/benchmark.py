"""The wake-word benchmark: every model on the same held-out tests, side by side (docs/specs/wake-word.md, W7-W8).
Each model is scored as pi-server runs it (pymicro-wakeword, streaming, its 5-frame average), on audio no model
trained on, with pymicro-wakeword from any venv (the trainer's on the playground):

    python benchmark.py <data root> out/hey_pi.json out/xiao_pai.json out/xiao_pai_02.json … > results/benchmark/results.txt

  false wakes   the same streams for every model, whatever its word, each played as one stream (a wake, then 1 s
                before another counts): the owner's room on the Pi's mic (the 10 held-out min), the owner's short
                videos (15 held-out min), Mandarin meetings (AliMeeting's test set, 2 h), everyday speech from the
                unheard voices (30 min), AudioSet sounds (35 min)
  recall        the model's own word from the 51 held-out synthetic voices (designed, cloned), clean and with
                background at 10 and 5 dB; 小派小派 also from spike 01's two held-out voices and the owner (the Pi's
                mic: quiet, a video playing; the Mac's). "Hey Pi" has no held-out owner clips (all 171 trained on)
  near misses   the model's own: the 51 voices' near misses (小派小派: the core 30 and the doubled 25; "Hey Pi":
                phrases/hey_pi.yaml), spike 01's, the owner's on the Pi, the owner's other lines; 小派小派 also the
                owner's "Hey Pi"s played back to back (false wakes per hour)

The comparison point is industry practice: each model's lowest cutoff at which the false-wake streams together
(3.5 h) give at most TARGET false wakes an hour, and everything else at that cutoff; then a grid of fixed cutoffs.
Per-model scores are cached in WORK/benchmark/ (by the model file's hash), so adding a model reruns only it.
"""

import collections
import hashlib
import json
import os
import sys
import wave
import zlib
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

ROOT = Path(sys.argv[1])                                  # the data root: xiao_pai/, datasets/, positives/
MODELS = [Path(m) for m in sys.argv[2:]]
DATA = ROOT / "xiao_pai"
EVAL = DATA / "eval"
HERE = Path(__file__).resolve().parent
WORK = Path(os.environ.get("WORK", HERE / "work"))
CACHE = WORK / "benchmark"
TARGET = 0.5                                              # false wakes an hour at the comparison point
GRID = np.round(np.arange(0.50, 0.9951, 0.005), 3)
SHOWN = (0.90, 0.95, 0.97, 0.99)
STEP = 160                                                # 10 ms at 16 kHz


def read(path: Path) -> np.ndarray:
    with wave.open(str(path)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32)


def glob(d: Path, pattern: str = "*.wav", part: slice = slice(None)) -> list[Path]:
    return sorted(d.glob(pattern))[part]


NOISE = glob(WORK / "audioset_16k", part=slice(0, 200))  # background for the recall tests; 200:400 is a stream

STREAMS = {                                               # every model, whatever its word
    "the owner's room, Pi mic (10 min held out)": glob(EVAL / "me-pi-room"),
    "the owner's short videos (15 min held out)": glob(EVAL / "video"),
    "Mandarin meetings, AliMeeting test (2 h)": glob(ROOT / "datasets/alimeeting/test_far_ch0"),
    "everyday speech, unheard voices (30 min)": glob(EVAL / "speech") + glob(EVAL / "tts3-everyday"),
    "AudioSet sounds (35 min)": glob(WORK / "audioset_16k", part=slice(200, 400)),
}
SUITES = {
    "小派小派": {
        "recall": {"51 unheard voices, designed": glob(EVAL / "tts3-positive", "d*.wav"),
                   "51 unheard voices, cloned": glob(EVAL / "tts3-positive", "c*.wav"),
                   "spike 01's 2 unheard voices": glob(EVAL / "positive"),
                   "the owner, Pi mic, quiet": glob(EVAL / "me-pi-positive", "pi-wake-2*.wav"),
                   "the owner, Pi mic, a video playing": glob(EVAL / "me-pi-positive", "pi-wake-bg-*.wav"),
                   "the owner, Mac mic": glob(EVAL / "me-positive")},
        "noisy": ("51 unheard voices, designed", "51 unheard voices, cloned", "spike 01's 2 unheard voices"),
        "near": {"51 voices, core 30 (小胖小胖, 小怕小怕 …)": glob(EVAL / "tts3-near"),
                 "51 voices, doubled 25 (小快小快, 小派小心 …)": glob(EVAL / "tts4-doubled"),
                 "spike 01's near misses": glob(EVAL / "near_miss"),
                 "the owner's near misses, Pi mic": glob(EVAL / "me-pi-near"),
                 "the owner's other lines, Mac mic": glob(EVAL / "me-negative")},
        "near_streams": {"the owner's 'Hey Pi's back to back (4 min)": glob(ROOT / "positives/me")},
    },
    "Hey Pi": {
        "recall": {"51 unheard voices, designed": glob(EVAL / "heypi-positive", "d*.wav"),
                   "51 unheard voices, cloned": glob(EVAL / "heypi-positive", "c*.wav")},
        "noisy": ("51 unheard voices, designed", "51 unheard voices, cloned"),
        "near": {"51 voices, Hey Pi's 18 (Hey pal, Apple pie …)": glob(EVAL / "heypi-near")},
        "near_streams": {},
    },
}


# ---- scoring ----------------------------------------------------------------------------------------------------
def probabilities(model_path: Path, x: np.ndarray) -> np.ndarray:
    """The model's 5-frame average after each 10 ms step, streaming, from a fresh state."""
    from pymicro_wakeword import MicroWakeWord, MicroWakeWordFeatures

    model, features = MicroWakeWord.from_config(model_path), MicroWakeWordFeatures()
    out = []
    pcm = np.clip(x, -32768, 32767).astype(np.int16).tobytes()
    for i in range(0, len(pcm) - 2 * STEP + 1, 2 * STEP):
        for f in features.process_streaming(pcm[i:i + 2 * STEP]):
            model.process_streaming(f)
            p = list(model._probabilities)
            if len(p) >= model.sliding_window_size:
                out.append(sum(p) / len(p))
    return np.array(out or [0.0])


def peak(model_path: Path, x: np.ndarray) -> float:
    z = np.zeros(16000, np.float32)
    return float(probabilities(model_path, np.concatenate([z, x, z])).max())


def mix(x: np.ndarray, name: str, snr_db: float) -> np.ndarray:
    noise = read(NOISE[zlib.crc32(name.encode()) % len(NOISE)])            # the same noise for every model
    if len(noise) < len(x):
        noise = np.tile(noise, len(x) // len(noise) + 1)
    n = noise[:len(x)]
    return x + np.sqrt((x ** 2).mean() / max((n ** 2).mean(), 1e-9) / 10 ** (snr_db / 10)) * n


def wakes(p: np.ndarray, cutoff: float) -> int:
    n, last = 0, -10 ** 9
    for i in np.flatnonzero(p >= cutoff):
        if i >= last + 100:
            n, last = n + 1, i
    return n


def stream(files: list[Path]) -> np.ndarray:
    return np.concatenate([np.concatenate([read(f), np.zeros(8000, np.float32)]) for f in files])[:16000 * 3600 * 2]


def score(model_path: Path) -> dict:
    """Every test for one model: peaks per clip, wakes per grid cutoff per stream (cached by the model's hash)."""
    word = json.loads(model_path.read_text())["wake_word"]
    tflite = model_path.with_suffix(".tflite")
    sets = {k: len(v) for part in ("recall", "near", "near_streams") for k, v in SUITES[word][part].items()}
    sets |= {k: len(v) for k, v in STREAMS.items()}                    # new clips in a set: scored again
    key = hashlib.sha256(tflite.read_bytes() + json.dumps(sets, sort_keys=True).encode()).hexdigest()[:16]
    cache = CACHE / f"{model_path.stem}-{key}.json"
    if cache.exists():
        return json.loads(cache.read_text())
    suite = SUITES[word]
    out = {"model": model_path.stem, "word": word, "recall": {}, "near": {}, "streams": {}, "near_streams": {}}
    for name, files in suite["recall"].items():
        if not files:
            continue
        clips = [(f.name, read(f)) for f in files]
        out["recall"][name] = [peak(model_path, x) for _, x in clips]
        if name in suite["noisy"]:
            for snr in (10, 5):
                out["recall"][f"{name} + background {snr} dB"] = [peak(model_path, mix(x, n, snr)) for n, x in clips]
    for name, files in suite["near"].items():
        if files:
            out["near"][name] = [peak(model_path, read(f)) for f in files]
    for group, streams in (("streams", STREAMS), ("near_streams", suite["near_streams"])):
        for name, files in streams.items():
            if files:
                x = stream(files)
                p = probabilities(model_path, x)
                out[group][name] = {"hours": len(x) / 16000 / 3600, "wakes": [wakes(p, c) for c in GRID]}
    CACHE.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(out))
    print(f"scored {model_path.stem} ({word})", file=sys.stderr, flush=True)
    return out


# ---- the tables -------------------------------------------------------------------------------------------------
def at(cutoff: float) -> int:
    return int(np.argmin(np.abs(GRID - cutoff)))


def operating_point(r: dict) -> int:
    hours = sum(s["hours"] for s in r["streams"].values())
    for i in range(len(GRID)):
        if sum(s["wakes"][i] for s in r["streams"].values()) / hours <= TARGET:
            return i
    return len(GRID) - 1


def table(title: str, rows: list[tuple[str, list[str]]], names: list[str]) -> None:
    w = max([38] + [len(n) + 1 for n, _ in rows])
    print(f"\n{title}\n  {'':{w}s}" + "".join(f"{n:>14s}" for n in names))
    for name, cells in rows:
        print(f"  {name:{w}s}" + "".join(f"{c:>14s}" for c in cells))


def report(results: list[dict], index: list[int], label: str) -> None:
    names = [r["model"] for r in results]
    hours = [sum(s["hours"] for s in r["streams"].values()) for r in results]
    rows = [("cutoff", [f"{GRID[i]:.3f}" for i in index]),
            ("false wakes per hour, all streams", [f"{sum(s['wakes'][i] for s in r['streams'].values()) / h:.2f}"
                                                  for r, i, h in zip(results, index, hours)])]
    rows += [(f"  {s}", [f"{r['streams'][s]['wakes'][i] / r['streams'][s]['hours']:.1f}" if s in r["streams"] else "-"
                         for r, i in zip(results, index)]) for s in STREAMS]
    table(f"=== {label} ===\nFALSE WAKES (per hour; the same audio for every model)", rows, names)
    recall_names = list(dict.fromkeys(k for r in results for k in r["recall"]))
    table("RECALL: the model's own word, share woken",
          [(n, [f"{np.mean(np.array(r['recall'][n]) >= GRID[i]):.0%}" if n in r["recall"] else "-"
                for r, i in zip(results, index)]) for n in recall_names], names)
    near_names = list(dict.fromkeys(k for r in results for k in r["near"]))
    rows = [(n, [f"{np.mean(np.array(r['near'][n]) >= GRID[i]):.0%}" if n in r["near"] else "-"
                 for r, i in zip(results, index)]) for n in near_names]
    stream_names = list(dict.fromkeys(k for r in results for k in r["near_streams"]))
    rows += [(f"{n} (per hour)", [f"{r['near_streams'][n]['wakes'][i] / r['near_streams'][n]['hours']:.1f}"
                                  if n in r["near_streams"] else "-" for r, i in zip(results, index)]) for n in stream_names]
    table("NEAR MISSES: the model's own, share that wakes it", rows, names)


if __name__ == "__main__":
    with ProcessPoolExecutor(max_workers=min(len(MODELS), os.cpu_count() or 1)) as pool:
        results = list(pool.map(score, MODELS))
    sizes = {k: len(v) for suite in SUITES.values() for part in ("recall", "near") for k, v in suite[part].items()}
    print(f"Wake-word benchmark: {len(results)} models, the same held-out audio (benchmark.py). Clips per set: "
          + ", ".join(f"{k} {v}" for k, v in sizes.items() if v))
    report(results, [operating_point(r) for r in results],
           f"EACH MODEL AT ITS OWN CUTOFF FOR AT MOST {TARGET} FALSE WAKES AN HOUR (the comparison point)")
    for c in SHOWN:
        report(results, [at(c)] * len(results), f"EVERY MODEL AT CUTOFF {c:.2f}")
