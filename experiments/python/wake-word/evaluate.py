"""The common test: every wake-word model on the same test set, each with its own table of the same rows (what it was
tested on) and columns (cutoffs), scored as pi-server runs it (pymicro-wakeword, streaming, its 5-frame average).
docs/specs/wake-word.md (W7-W8) and each spike's results/ use these tables. With any venv that has pymicro-wakeword
(the trainer's on the playground):

    python evaluate.py <data root> out/xiao_pai_04b.json [more models …] [--out results]

`--out results` writes each table into its spike's folder too (results/<spike>/test-<model>.txt).

  the test set  testset.py: fixed, rendered once, never trained on (TESTSET=v1): the unheard voices' clips each in one
                listening condition (40% quiet, 25% room noise, 25% a video, TV or meeting, 10% very noisy; test-only
                background), the owner's held-out clips as recorded; the same mix for near misses
  false wakes   per hour, the same streams for every model whatever its word, each played as one stream (a wake,
                then 1 s before another counts), all test-only: the owner's room on the Pi's mic (10 held-out min),
                the owner's short videos (15 held-out min), Mandarin meetings (AliMeeting's test set, 2 h), everyday
                speech from the unheard voices (30 min); and the other wake word said back to back (test-only)
  recall        its word's test-set clips by set, then the unheard voices together by condition
  near misses   the same, for its near misses
  certainty     the peak score each set gets (median, and the 10th percentile: its weakest tenth), and its near
                misses' (median, 90th percentile); a model that scores its word at 0.99 is surer than one that barely
                passes 0.91, with more margin for a mic, voice or room no test has. The margin: the unheard voices'
                10th percentile minus the near misses' 90th

The last column is industry practice for comparing models: the model at its lowest cutoff with at most TARGET false
wakes an hour over all the streams together. Scores are cached in WORK/evaluate/ (by the model's hash, the test set's
version and the streams), so a rerun only scores what's new.
"""

import hashlib
import json
import os
import sys
import wave
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

ROOT = Path(sys.argv[1])                                  # the data root: testset/, datasets/
MODELS = [Path(m) for m in sys.argv[2:] if m.endswith(".json")]
TE = ROOT / "datasets/processed/test"                     # test-only (train.py refuses it)
TESTSET = ROOT / "testset" / os.environ.get("TESTSET", "v1")
HERE = Path(__file__).resolve().parent
WORK = Path(os.environ.get("WORK", HERE / "work"))
CACHE = WORK / "evaluate"
TARGET = 0.5                                              # false wakes an hour at the comparison point
GRID = np.round(np.arange(0.50, 0.9951, 0.005), 3)
CUTOFFS = (0.80, 0.90, 0.95, 0.97, 0.99)
CONDITIONS = ("quiet", "room noise", "video, TV or meeting", "very noisy")
SPIKES = {"hey_pi": "00-hey-pi", "xiao_pai": "01-xiao-pai", "xiao_pai_02": "02-mandarin-noise",
          "xiao_pai_03": "03-many-voices"}                # and xiao_pai_04* → 04-pi-mic
STEP = 160                                                # 10 ms at 16 kHz


def read(path: Path) -> np.ndarray:
    with wave.open(str(path)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32)


def glob(d: Path, pattern: str = "*.wav") -> list[Path]:
    return sorted(d.glob(pattern))


STREAMS = {                                               # every model, whatever its word; all test-only audio
    "the owner's room, Pi mic (10 min held out)": glob(TE / "room-me-pi"),
    "the owner's short videos (15 min held out)": glob(TE / "video-me"),
    "Mandarin meetings, AliMeeting test (2 h)": glob(TE / "alimeeting-test"),
    "everyday speech, unheard voices (30 min)": glob(TE / "speech-tts-01") + glob(TE / "speech-tts-03"),
}
NEAR_STREAMS = {  # the other wake word said back to back (0.5 s apart): "…Pi. Hey Pi…" doubles a syllable, as 小派小派 does
    "小派小派": {"'Hey Pi' back to back, 51 unheard voices": glob(TE / "heypi-tts-03")},
    "Hey Pi": {"'小派小派' back to back, 51 unheard voices": glob(TE / "xiaopai-tts-03")},
}
MANIFEST = [json.loads(line) for line in open(TESTSET / "manifest.jsonl")]


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


def wakes(p: np.ndarray, cutoff: float) -> int:
    n, last = 0, -10 ** 9
    for i in np.flatnonzero(p >= cutoff):
        if i >= last + 100:
            n, last = n + 1, i
    return n


def stream(files: list[Path]) -> np.ndarray:
    return np.concatenate([np.concatenate([read(f), np.zeros(8000, np.float32)]) for f in files])[:16000 * 3600 * 2]


def score(model_path: Path) -> dict:
    """Every test for one model: its word's test-set clips (a peak each), the streams (wakes per grid cutoff); cached
    by the model's hash, the test set's version and the streams."""
    word = json.loads(model_path.read_text())["wake_word"]
    tflite = model_path.with_suffix(".tflite")
    clips = [m for m in MANIFEST if m["labels"].get(word)]
    streams = {**STREAMS, **NEAR_STREAMS[word]}
    ident = {"testset": TESTSET.name, "clips": len(clips), "labels": "per word",
             "streams": {k: len(v) for k, v in streams.items()}}
    key = hashlib.sha256(tflite.read_bytes() + json.dumps(ident, sort_keys=True).encode()).hexdigest()[:16]
    cache = CACHE / f"{model_path.stem}-{key}.json"
    if cache.exists():
        return json.loads(cache.read_text())
    out = {"model": model_path.stem, "word": word, "testset": TESTSET.name, "clips": [], "streams": {}, "near_streams": {}}
    for m in clips:
        out["clips"].append({"kind": m["labels"][word], "set": m["set"], "word": m["word"], "condition": m["condition"],
                             "peak": peak(model_path, read(TESTSET / "audio" / m["clip"]))})
    for group, named in (("streams", STREAMS), ("near_streams", NEAR_STREAMS[word])):
        for name, files in named.items():
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
    out = [f"{r['model']} ({r['word']}): the common test (evaluate.py), test set {r['testset']} (testset.py): audio no model trained on",
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
    def groups(kind: str) -> tuple[dict, dict]:
        sets, conds = {}, {}
        for c in r["clips"]:
            if c["kind"] == kind:
                sets.setdefault(c["set"], []).append(c["peak"])
                if c["condition"] != "as recorded":
                    conds.setdefault(c["condition"], []).append(c["peak"])
        return sets, {k: conds[k] for k in CONDITIONS if k in conds}

    recall, recall_by = groups("positive")
    near, near_by = groups("near")
    out.append(f"\nRECALL: its word, share woken (test set {r['testset']}: the unheard voices 40% quiet, 25% room noise, "
               f"25% a video, TV or meeting, 10% very noisy; the owner as recorded)\n  {'':{w}s}{head}")
    for name, v in recall.items():
        row(f"{name} ({len(v)})", share(v))
    out.append("  the unheard voices together, by condition:")
    for name, v in recall_by.items():
        row(f"  {name} ({len(v)})", share(v))
    out.append(f"\nNEAR MISSES: share that wakes it (the same mix)\n  {'':{w}s}{head}")
    for name, v in near.items():
        row(f"{name} ({len(v)})", share(v))
    out.append("  the synthetic near misses together, by condition:")
    for name, v in near_by.items():
        row(f"  {name} ({len(v)})", share(v))
    for name, s in r["near_streams"].items():
        row(f"{name}, per hour", per_hour(s))
    others = {}
    for c in r["clips"]:
        if c["kind"] in ("other wake word", "other"):
            others.setdefault(f"{c['word']}: {c['set']}", []).append(c["peak"])
    if others:
        out.append(f"\nTHE OTHER WAKE WORD'S CLIPS: share that wakes it (its word and its near misses)\n  {'':{w}s}{head}")
        for name, v in others.items():
            row(f"{name} ({len(v)})", share(v))

    def pct(values, qs) -> list[str]:
        return [f"{np.percentile(values, q):.3f}" for q in qs]

    def rows3(title: str, qs: tuple, labels: tuple, sets: dict) -> None:
        out.append(f"\n{title}\n  {'':{w}s}" + "".join(f"{l:>8s}" for l in labels))
        for name, v in sets.items():
            label = f"{name} ({len(v)})"
            out.append(f"  {label:{w}s}" + "".join(f"{c:>8s}" for c in pct(v, qs)))

    rows3("CERTAINTY: the peak score its word gets (1.00 = sure; the higher, the more margin for audio no test has)",
          (50, 25, 10), ("median", "p25", "p10"), recall | {f"unheard voices, {k}": v for k, v in recall_by.items()})
    rows3("… and its near misses (the lower, the surer it isn't the word)", (50, 75, 90), ("median", "p75", "p90"), near)
    voices = [x for v in recall_by.values() for x in v]
    misses = [x for v in near.values() for x in v]
    if voices and misses:
        out.append(f"  {'margin: unheard voices p10 − near misses p90':{w}s}"
                   f"{np.percentile(voices, 10) - np.percentile(misses, 90):>8.3f}   (above 0: they don't overlap)")
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    out_dir = Path(sys.argv[sys.argv.index("--out") + 1]) if "--out" in sys.argv else None
    workers = int(os.environ.get("WORKERS", 4))           # each holds hours of audio as a stream: memory, not cores
    with ProcessPoolExecutor(max_workers=min(len(MODELS), workers)) as pool:
        results = list(pool.map(score, MODELS))
    for r in results:
        text = report(r)
        print(text)
        if out_dir:
            spike = SPIKES.get(r["model"], "04-pi-mic" if r["model"].startswith("xiao_pai_04") else None)
            if spike:
                (out_dir / spike).mkdir(parents=True, exist_ok=True)
                (out_dir / spike / f"test-{r['model']}.txt").write_text(text)
