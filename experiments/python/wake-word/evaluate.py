"""The common test: every wake-word model on the same held-out audio, each with its own table of the same rows (what
it was tested on) and columns (cutoffs), as pi-server runs it (pymicro-wakeword, streaming, its 5-frame average).
docs/specs/wake-word.md (W7-W8) and each spike's results/ use these tables. With any venv that has pymicro-wakeword
(the trainer's on the playground):

    python evaluate.py <data root> out/xiao_pai_04b.json [more models …] [--out results]

`--out results` writes each table into its spike's folder too (results/<spike>/test-<model>.txt).

  false wakes   per hour, the same streams for every model whatever its word, each played as one stream (a wake,
                then 1 s before another counts): the owner's room on the Pi's mic (the 10 held-out min), the
                owner's short videos (15 held-out min), Mandarin meetings (AliMeeting's test set, 2 h), everyday
                speech from the unheard voices (30 min), AudioSet sounds (35 min)
  recall        the model's own word from the 51 held-out synthetic voices (designed, cloned), clean and with
                background at 10 and 5 dB; 小派小派 also from spike 01's two held-out voices and the owner (the Pi's
                mic: quiet, a video playing; the Mac's). "Hey Pi" has no held-out owner clips (all 171 trained on)
  near misses   the model's own: the 51 voices' near misses (小派小派: the core 30 and the doubled 25; "Hey Pi":
                phrases/hey_pi.yaml), spike 01's, the owner's on the Pi, the owner's other lines; 小派小派 also the
                owner's "Hey Pi"s played back to back (false wakes per hour)

Certainty: the peak score each set gets (median, and the 10th percentile: its weakest tenth), and its near misses
(median, 90th percentile); a model that scores its word at 0.99 is surer than one that barely passes 0.91, with
more margin for a mic, voice or room no test has. The margin: the unheard voices' 10th percentile minus the near
misses' 90th.

The last column is industry practice for comparing models: the model at its lowest cutoff with at most TARGET false
wakes an hour over all the streams together (3.5 h). Scores are cached in WORK/evaluate/ (by the model file's hash
and the sets' sizes), so a rerun only scores what's new.
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
MODELS = [Path(m) for m in sys.argv[2:] if m.endswith(".json")]
DATA = ROOT / "xiao_pai"
EVAL = DATA / "eval"
HERE = Path(__file__).resolve().parent
WORK = Path(os.environ.get("WORK", HERE / "work"))
CACHE = WORK / "evaluate"
TARGET = 0.5                                              # false wakes an hour at the comparison point
GRID = np.round(np.arange(0.50, 0.9951, 0.005), 3)
CUTOFFS = (0.80, 0.90, 0.95, 0.97, 0.99)
SPIKES = {"hey_pi": "00-hey-pi", "xiao_pai": "01-xiao-pai", "xiao_pai_02": "02-mandarin-noise",
          "xiao_pai_03": "03-many-voices"}                # and xiao_pai_04* → 04-pi-mic
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
def operating_point(r: dict) -> int:
    hours = sum(s["hours"] for s in r["streams"].values())
    for i in range(len(GRID)):
        if sum(s["wakes"][i] for s in r["streams"].values()) / hours <= TARGET:
            return i
    return len(GRID) - 1


def report(r: dict) -> str:
    """One model's table: a section per kind of test, a row per set, a column per cutoff, its comparison point last."""
    cols = [int(np.argmin(np.abs(GRID - c))) for c in CUTOFFS] + [operating_point(r)]
    head = "".join(f"{c:>8.2f}" for c in CUTOFFS) + f"{'≤' + str(TARGET) + '/h':>10s}"
    w = 52
    hours = sum(s["hours"] for s in r["streams"].values())
    rate = sum(s["wakes"][cols[-1]] for s in r["streams"].values()) / hours
    point = (f"{GRID[cols[-1]]:.3f}" if rate <= TARGET
             else f"never reached (at {GRID[cols[-1]]:.3f}, the highest tried: {rate:.2f} an hour)")
    out = [f"{r['model']} ({r['word']}): the common test (evaluate.py), audio no model trained on",
           f"last column: its cutoff for at most {TARGET} false wakes an hour over all the streams: {point}"]

    def row(name: str, cells: list[str]) -> None:
        out.append(f"  {name:{w}s}" + "".join(f"{c:>8s}" for c in cells[:-1]) + f"{cells[-1]:>10s}")

    def share(values) -> list[str]:
        v = np.array(values)
        return [f"{np.mean(v >= GRID[i]):.0%}" for i in cols]

    def per_hour(s: dict) -> list[str]:
        return [f"{s['wakes'][i] / s['hours']:.1f}" for i in cols]

    out.append(f"\nFALSE WAKES PER HOUR, each set as one stream\n  {'':{w}s}{head}")
    for name, s in r["streams"].items():
        row(name, per_hour(s))
    hours = sum(s["hours"] for s in r["streams"].values())
    row(f"all together ({hours:.1f} h)", [f"{sum(s['wakes'][i] for s in r['streams'].values()) / hours:.2f}" for i in cols])
    out.append(f"\nRECALL: its word, share woken\n  {'':{w}s}{head}")
    for name, v in r["recall"].items():
        row(f"{name} ({len(v)})" if " + " not in name else f"  + {name.split(' + ')[-1]}", share(v))
    out.append(f"\nNEAR MISSES: share that wakes it\n  {'':{w}s}{head}")
    for name, v in r["near"].items():
        row(f"{name} ({len(v)})", share(v))
    for name, s in r["near_streams"].items():
        row(f"{name}, per hour", per_hour(s))

    def pct(values, qs) -> list[str]:
        return [f"{np.percentile(values, q):.3f}" for q in qs]

    def rows3(title: str, qs: tuple, labels: tuple, sets: dict) -> None:
        out.append(f"\n{title}\n  {'':{w}s}" + "".join(f"{l:>8s}" for l in labels))
        for name, v in sets.items():
            label = f"{name} ({len(v)})" if " + " not in name else f"  + {name.split(' + ')[-1]}"
            out.append(f"  {label:{w}s}" + "".join(f"{c:>8s}" for c in pct(v, qs)))

    rows3("CERTAINTY: the peak score its word gets (1.00 = sure; the higher, the more margin for audio no test has)",
          (50, 25, 10), ("median", "p25", "p10"), r["recall"])
    rows3("… and its near misses (the lower, the surer it isn't the word)", (50, 75, 90), ("median", "p75", "p90"), r["near"])
    clean = [x for n, v in r["recall"].items() if " + " not in n and "owner" not in n for x in v]
    near = [x for v in r["near"].values() for x in v]
    if clean and near:
        out.append(f"  {'margin: unheard voices p10 − near misses p90':{w}s}"
                   f"{np.percentile(clean, 10) - np.percentile(near, 90):>8.3f}   (above 0: they don't overlap)")
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    out_dir = Path(sys.argv[sys.argv.index("--out") + 1]) if "--out" in sys.argv else None
    with ProcessPoolExecutor(max_workers=min(len(MODELS), os.cpu_count() or 1)) as pool:
        results = list(pool.map(score, MODELS))
    for r in results:
        text = report(r)
        print(text)
        if out_dir:
            spike = SPIKES.get(r["model"], "04-pi-mic" if r["model"].startswith("xiao_pai_04") else None)
            if spike:
                (out_dir / spike).mkdir(parents=True, exist_ok=True)
                (out_dir / spike / f"test-{r['model']}.txt").write_text(text)
