# Copied from pi-server/spikes/01-stt-on-pi/prep_data.py (the Pi spike, 2026-11-17), unchanged but for this
# line: the same test set, so the GPU numbers compare with the Pi's.
"""Build the spike's test set: 16 kHz mono WAVs + a manifest with reference text.

    en   FLEURS en_us test        (read speech, Google; the set Whisper reports on)
    zh   FLEURS cmn_hans_cn test  (read Mandarin, simplified characters)
    mix  ASCEND test, language == "mixed"  (real conversational Mandarin-English code-switching)

The FLEURS tarballs are streamed and abandoned after N clips, so only a slice is downloaded.

    python prep_data.py --out ~/stt-spike/data --n 40
"""

import argparse
import csv
import io
import json
import tarfile
import urllib.request
from pathlib import Path

import numpy as np
import soundfile as sf

HF = "https://huggingface.co/datasets"
FLEURS = {"en": "en_us", "zh": "cmn_hans_cn"}
ASCEND = f"{HF}/CAiRE/ASCEND/resolve/refs%2Fconvert%2Fparquet/main/test/0000.parquet"


def fetch(url: str) -> bytes:
    with urllib.request.urlopen(url) as r:
        return r.read()


def write_wav(path: Path, data: bytes) -> float:
    audio, sr = sf.read(io.BytesIO(data), dtype="float32")
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    assert sr == 16000, f"{path}: {sr} Hz"
    sf.write(path, audio, sr, subtype="PCM_16")
    return len(audio) / sr


def fleurs(lang: str, out: Path, n: int, max_s: float) -> list[dict]:
    code = FLEURS[lang]
    tsv = fetch(f"{HF}/google/fleurs/resolve/main/data/{code}/test.tsv").decode()
    # id, file_name, raw_transcription, transcription, phonemes, num_samples, gender
    refs = {row[1]: row[2] for row in csv.reader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE)}
    rows, seen = [], set()
    url = f"{HF}/google/fleurs/resolve/main/data/{code}/audio/test.tar.gz"
    with urllib.request.urlopen(url) as r, tarfile.open(fileobj=r, mode="r|gz") as tar:
        for m in tar:
            name = Path(m.name).name
            if not m.isfile() or name not in refs:
                continue
            data = tar.extractfile(m).read()
            text = refs[name]
            if text in seen:  # FLEURS has several speakers per sentence; keep sentences distinct
                continue
            path = out / f"{lang}-{len(rows):03d}.wav"
            dur = write_wav(path, data)
            if dur > max_s:
                path.unlink()
                continue
            seen.add(text)
            rows.append({"id": path.stem, "lang": lang, "path": path.name, "text": text, "dur": round(dur, 2)})
            if len(rows) == n:
                break
    return rows


def ascend(out: Path, n: int, min_s: float, max_s: float) -> list[dict]:
    import pyarrow.parquet as pq

    table = pq.read_table(io.BytesIO(fetch(ASCEND)))
    rows = []
    for r in table.to_pylist():
        if r["language"] != "mixed" or not (min_s <= r["duration"] <= max_s):
            continue
        path = out / f"mix-{len(rows):03d}.wav"
        dur = write_wav(path, r["audio"]["bytes"])
        rows.append({"id": path.stem, "lang": "mix", "path": path.name, "text": r["transcription"], "dur": round(dur, 2)})
        if len(rows) == n:
            break
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--n", type=int, default=40)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    rows = fleurs("en", args.out, args.n, 15) + fleurs("zh", args.out, args.n, 15) + ascend(args.out, args.n, 2.0, 10.0)
    with open(args.out / "manifest.jsonl", "w") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    for lang in ("en", "zh", "mix"):
        d = [r["dur"] for r in rows if r["lang"] == lang]
        print(f"{lang}: {len(d)} clips, {sum(d) / 60:.1f} min, median {np.median(d):.1f} s")


if __name__ == "__main__":
    main()
