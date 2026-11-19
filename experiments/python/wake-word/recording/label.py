"""Your recordings, labelled: each take in data/xiao_pai/recordings split into utterances (segment.py), each
utterance through speech-to-text (speech-service, through its test client), all of it in one manifest,
data/xiao_pai/recordings/transcripts.jsonl: clip, take, start and end in the take, duration, kind, what was
said, the language heard, and where the label came from. For the wake word now; for training a voice of your
own later (audio with its text). Run with speech-service's venv, its test client running:

    ../../../python/services/speech-service/.venv/bin/python recording/label.py

A take already in the manifest is skipped (a rerun labels only new takes). Each clip gets speech-to-text's
transcript (`asr`) and the closest line of the bank you read from (`match`, `match_score`: phrases/
xiao_pai_bank.yaml, that take's language); `text` is the bank's line when it matches closely (score 0.75 and
up, `label: "bank"`: what you were reading, exactly) and the transcript otherwise (`label: "asr"`: something
you said in your own words). The wake word's clips are `text: 小派小派` (`label: "intended"`). Short-video and
ambient takes aren't split (no one is talking to the mic); they're listed whole.
"""

import asyncio
import difflib
import json
import re
import sys
import wave
from pathlib import Path

import numpy as np
import websockets
import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from segment import read, save, split  # noqa: E402

RECORDINGS = HERE.parent / "data/xiao_pai/recordings"
MANIFEST = RECORDINGS / "transcripts.jsonl"
CLIENT = "ws://localhost:8765"
KINDS = {  # take name → (kind, longest utterance in s, the language speech-to-text is told, intended text)
    "mac-wake-": ("wake", 2.5, "Chinese", "小派小派"),
    "mac-zh-": ("zh", 10.0, "Chinese", None),        # Chinese mode still writes the English words of mixed lines
    "mac-en-": ("en", 10.0, "English", None),        # detection took a short "Hey Pi" for Cantonese
}
CJK = re.compile(r"[\u4e00-\u9fff]")


def bank_lines() -> dict[str, list[str]]:
    """The bank as the recording page splits it: Chinese (near misses, everyday, mixed) and English."""
    b = yaml.safe_load(open(HERE.parent / "phrases/xiao_pai_bank.yaml"))
    near = b["near_miss_core"] + b["near_miss_extra"]
    return {"zh": [t for t in near if CJK.search(t)] + b["zh"] + b["mixed"],
            "en": [t for t in near if not CJK.search(t)] + b["en"]}


def plain(text: str) -> str:
    return re.sub(r"[^\w]", "", text.lower())


def match(text: str, lines: list[str]) -> tuple[str, float]:
    t = plain(text)
    best = max(lines, key=lambda line: difflib.SequenceMatcher(None, t, plain(line)).ratio())
    return best, round(difflib.SequenceMatcher(None, t, plain(best)).ratio(), 2)


async def hear(pcm: bytes, language: str | None) -> tuple[str, str]:
    q = f"?language={language}" if language else ""
    async with websockets.connect(f"{CLIENT}/v1/realtime/transcription{q}", max_size=None) as ws:
        e = json.loads(await ws.recv())
        while e["type"] == "queued":
            e = json.loads(await ws.recv())
        for off in range(0, len(pcm), 3200):
            await ws.send(pcm[off:off + 3200])
        await ws.send(json.dumps({"type": "commit"}))
        async for m in ws:
            e = json.loads(m)
            if e["type"] == "final":
                return e.get("text", ""), e.get("language", "")
            if e["type"] == "error":
                raise RuntimeError(e)
    return "", ""


async def main():
    done = set()
    if MANIFEST.exists():
        done = {json.loads(line)["take"] for line in MANIFEST.read_text().splitlines() if line.strip()}
    takes = sorted(p for p in RECORDINGS.glob("mac-*.wav") if p.name not in done)
    bank = bank_lines()
    with MANIFEST.open("a") as out:
        for take in takes:
            kind = next((v for k, v in KINDS.items() if take.name.startswith(k)), None)
            x = read(take)
            if kind is None:                               # ambient, short videos: the whole take, unsplit
                kind_name = "video" if take.name.startswith("mac-video-") else "ambient"
                out.write(json.dumps({"clip": None, "take": take.name, "start": 0.0, "end": round(len(x) / 16000, 2),
                                      "duration": round(len(x) / 16000, 2), "kind": kind_name, "asr": None,
                                      "language": None, "text": None, "label": None}, ensure_ascii=False) + "\n")
                print(f"{take.name}: {kind_name}, listed whole ({len(x) / 16000:.0f} s)")
                continue
            name, longest, language, intended = kind
            clips_dir = RECORDINGS / "clips" / take.stem
            clips_dir.mkdir(parents=True, exist_ok=True)
            spans = split(x, auto=True, longest=longest, log=lambda *_: None)
            for k, (lo, hi) in enumerate(spans, 1):
                clip = clips_dir / f"{take.stem}-{k:03d}.wav"
                save(clip, x[lo:hi])
                asr, heard_language = await hear(x[lo:hi].tobytes(), language)
                row = {"clip": str(clip.relative_to(RECORDINGS)), "take": take.name, "start": round(lo / 16000, 2),
                       "end": round(hi / 16000, 2), "duration": round((hi - lo) / 16000, 2), "kind": name,
                       "asr": asr, "language": heard_language}
                if intended:
                    row |= {"text": intended, "label": "intended"}
                else:
                    line, score = match(asr, bank[name])
                    row |= {"match": line, "match_score": score}
                    row |= {"text": line, "label": "bank"} if score >= 0.75 else {"text": asr, "label": "asr"}
                out.write(json.dumps(row, ensure_ascii=False) + "\n")
            out.flush()
            print(f"{take.name}: {len(spans)} clips labelled → clips/{take.stem}/")


asyncio.run(main())
