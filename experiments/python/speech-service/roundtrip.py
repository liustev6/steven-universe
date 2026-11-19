"""Mixed Chinese/English with numbers, spoken by the live service and heard back (from spike 13: how
text normalization was measured; docs/specs/speech-service-spike 13).

Each sentence is spoken N times by each voice (sampling: each run differs), then transcribed back,
so how the TTS read it shows up as text; the speech's length is a babble check (a sentence spoken
in 11 s where 5 would do). Through the test client (it holds the token):

    cd python/services/speech-service
    .venv/bin/python test-client/server.py &
    .venv/bin/python ../../../experiments/python/speech-service/roundtrip.py --runs 3 --out /tmp/tn > roundtrip.txt

The service normalizes each sentence itself (T14), so this measures what callers get.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import wave
from pathlib import Path

import numpy as np
import websockets

SENTENCES = [
    "今天是2026年11月17日。",
    "英国的首都用英文说是London。",
    "这个耳机打折后是299元，便宜了15%。",
    "我的电话号码是138-0013-8000。",
    "现在气温是-5.5°C，明天会升到3°C。",
    "我昨天用iPhone 17给Steven发了一条message。",
    "The meeting is at 3:30 PM on March 5th, 2026.",
    "Mount Logan is 5,959 metres tall, or 19,551 feet.",
]

ap = argparse.ArgumentParser()
ap.add_argument("--client", default="ws://localhost:8765")
ap.add_argument("--voices", default="serena,ryan")
ap.add_argument("--runs", type=int, default=3)
ap.add_argument("--out", default=None, help="a folder for the WAVs")
args = ap.parse_args()


async def speak(text: str, voice: str) -> bytes:
    pcm = bytearray()
    async with websockets.connect(f"{args.client}/v1/realtime/speech?voice={voice}", max_size=None) as ws:
        async for m in ws:
            if isinstance(m, bytes):
                pcm += m
            elif json.loads(m)["type"] == "started":
                await ws.send(json.dumps({"type": "text", "text": text}))
                await ws.send(json.dumps({"type": "end"}))
    return bytes(pcm)


async def hear(pcm24: bytes) -> str:
    x = np.frombuffer(pcm24, np.int16).astype(np.float32)
    x16 = np.interp(np.arange(0, len(x) * 2 // 3) * 1.5, np.arange(len(x)), x).astype(np.int16)
    async with websockets.connect(f"{args.client}/v1/realtime/transcription", max_size=None) as ws:
        async for m in ws:
            e = json.loads(m)
            if e["type"] == "started":
                for i in range(0, len(x16), 16000):
                    await ws.send(x16[i:i + 16000].tobytes())
                await ws.send(json.dumps({"type": "commit"}))
            if e["type"] == "final":
                return e["text"]
    return "?"


async def main():
    out = Path(args.out) if args.out else None
    if out:
        out.mkdir(parents=True, exist_ok=True)
    for n, sentence in enumerate(SENTENCES, 1):
        print(f"\n[{n}] {sentence}")
        for voice in args.voices.split(","):
            for run in range(1, args.runs + 1):
                t = time.monotonic()
                pcm = await speak(sentence, voice)
                seconds = len(pcm) / 2 / 24000
                heard = await hear(pcm)
                print(f"  {voice:7} {run}  {seconds:4.1f} s  {heard}")
                if out:
                    with wave.open(str(out / f"{n:02d}-{voice}-{run}.wav"), "wb") as w:
                        w.setnchannels(1), w.setsampwidth(2), w.setframerate(24000), w.writeframes(pcm)
                sys.stdout.flush()


asyncio.run(main())
