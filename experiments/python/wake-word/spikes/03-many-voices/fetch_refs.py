"""Spike 03: a reference clip for each of AISHELL-3's 218 Mandarin speakers (Apache 2.0), for voice cloning
(Qwen3-TTS Base): one clean read sentence of 10-16 characters (about 3-5 s) per speaker, fetched alone from
the Hugging Face copy (no 19 GB archive), with its text and the speaker's age group, gender and accent.

    .venv/bin/python spikes/03-many-voices/fetch_refs.py data/datasets/aishell3-refs
"""

import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE = "https://huggingface.co/datasets/AISHELL/AISHELL-3/resolve/main"
out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)


def get(path: str) -> bytes:
    with urllib.request.urlopen(f"{BASE}/{path}", timeout=60) as r:
        return r.read()


info = {}
for line in get("spk-info.txt").decode().splitlines():
    if line and not line.startswith("#"):
        spk, age, gender, accent = line.split()[:4]
        info[spk] = {"age": {"A": "<14", "B": "14-25", "C": "26-40", "D": ">41"}.get(age, age), "gender": gender, "accent": accent}

candidates = {}                                            # speaker → its suitable sentences, in order
for split in ("train", "test"):
    for line in get(f"{split}/content.txt").decode("utf-8").splitlines():
        name, rest = line.split("\t", 1)
        spk = name[:7]
        text = "".join(re.findall(r"[\u4e00-\u9fff]", rest))          # the characters (the pinyin dropped)
        if 10 <= len(text) <= 16:
            candidates.setdefault(spk, []).append({"speaker": spk, "split": split, "file": f"{split}/wav/{spk}/{name}",
                                                   "text": text} | info.get(spk, {}))

rows, missing = [], []
for spk, options in sorted(candidates.items()):
    target = out / f"{spk}.wav"
    for row in options[:8]:                                # this copy lacks some files: the next sentence then
        try:
            if not target.exists():
                target.write_bytes(get(row["file"]))
            rows.append(row)
            break
        except urllib.error.HTTPError:
            continue
    else:
        missing.append(spk)
(out / "refs.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
genders = {g: sum(r.get("gender") == g for r in rows) for g in ("female", "male")}
print(f"{len(rows)} speakers → {out}: {genders}; none found for {len(missing)}: {missing[:10]}")
