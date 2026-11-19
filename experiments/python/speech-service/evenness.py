"""How evenly does the TTS speak? (spikes 14-15: how the model, its instruction and the voices were
chosen; docs/specs/speech-service-spikes.) Short replies, each spoken N times by each voice through
the live service (the test client holds the token); each clip measured from its audio:

    duration        the whole clip
    speech          time with sound (20 ms windows above -45 dBFS)
    pauses          silences of 150 ms or more inside the speech (not the lead-in or the tail); the longest
    rate            units per second of speech: Chinese characters, English syllables (vowel groups)
    heard           speech-to-text of the clip: babble shows as words that aren't there

    cd python/services/speech-service
    .venv/bin/python test-client/server.py &
    .venv/bin/python ../../../experiments/python/speech-service/evenness.py --runs 4 --voices ryan,dylan > evenness.txt
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import statistics
import sys
import time
from pathlib import Path

import numpy as np
import websockets

SENTENCES = [
    "好的。",
    "今天是星期二。",
    "客厅的灯已经关了。",
    "西雅图现在是晚上十点半，气温七度。",
    "明天上午多云，下午可能会下小雨，记得带伞。",
    "Sure.",
    "It's Tuesday.",
    "The kitchen lights are off.",
    "It's ten thirty at night in Seattle, and seven degrees.",
    "Tomorrow will be cloudy in the morning, with light rain in the afternoon, so take an umbrella.",
    "好的，我把客厅的lights关了。",
]

ap = argparse.ArgumentParser()
ap.add_argument("--client", default="ws://localhost:8765")
ap.add_argument("--voices", default="ryan,serena")
ap.add_argument("--runs", type=int, default=6)
ap.add_argument("--wavs", default=None, help="a folder to keep the clips in")
ap.add_argument("--language", default=None, help="sent with each sentence (else the service's default)")
ap.add_argument("--only", default=None, help="sentence numbers, e.g. 1,6,7,11")
args = ap.parse_args()

RATE = 24000
WIN = RATE // 50                                     # 20 ms


def units(text: str) -> int:
    """Spoken units: each Chinese character, and each English syllable (a vowel group)."""
    cjk = len(re.findall(r"[一-鿿]", text))
    latin = " ".join(re.findall(r"[A-Za-z']+", text)).lower()
    syllables = sum(max(1, len(re.findall(r"[aeiouy]+", w)) - (w.endswith("e") and len(w) > 2)) for w in latin.split())
    return cjk + syllables


def measure(pcm: bytes) -> dict:
    x = np.frombuffer(pcm, np.int16).astype(np.float32) / 32768
    n = len(x) // WIN
    db = 20 * np.log10(np.sqrt(np.mean(x[: n * WIN].reshape(n, WIN) ** 2, axis=1)) + 1e-9)
    voiced = db > -45
    idx = np.flatnonzero(voiced)
    if len(idx) == 0:
        return {"duration": len(x) / RATE, "speech": 0.0, "pauses": 0.0, "longest": 0.0, "lead": 0.0, "tail": 0.0}
    first, last = idx[0], idx[-1]
    inner = voiced[first:last + 1]
    pauses, run = [], 0
    for v in inner:
        if not v:
            run += 1
        elif run:
            pauses.append(run)
            run = 0
    long_pauses = [p * 0.02 for p in pauses if p * 0.02 >= 0.15]
    return {"duration": len(x) / RATE, "speech": (last - first + 1) * 0.02 - sum(long_pauses),
            "pauses": sum(long_pauses), "longest": max(long_pauses, default=0.0),
            "lead": first * 0.02, "tail": (n - 1 - last) * 0.02}


TIMES: list[tuple[float, float, float]] = []          # (first audio s, made in s, audio s) per clip


async def speak(text: str, voice: str) -> bytes:
    pcm = bytearray()
    lang = f"&language={args.language}" if args.language else ""
    sent = first = None
    async with websockets.connect(f"{args.client}/v1/realtime/speech?voice={voice}{lang}", max_size=None) as ws:
        async for m in ws:
            if isinstance(m, bytes):
                first = first or time.perf_counter()
                pcm += m
            elif json.loads(m)["type"] == "started":
                sent = time.perf_counter()
                await ws.send(json.dumps({"type": "text", "text": text}))
                await ws.send(json.dumps({"type": "end"}))
            elif json.loads(m)["type"] == "done":
                TIMES.append((first - sent, time.perf_counter() - sent, len(pcm) / 2 / RATE))
    return bytes(pcm)


async def hear(pcm: bytes) -> str:
    x = np.frombuffer(pcm, np.int16).astype(np.float32)
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
    out = Path(args.wavs) if args.wavs else None
    if out:
        out.mkdir(parents=True, exist_ok=True)
    rows = []
    only = {int(x) for x in args.only.split(",")} if args.only else None
    for n, text in enumerate(SENTENCES, 1):
        if only and n not in only:
            continue
        u = units(text)
        print(f"\n[{n}] {text}  ({u} units)")
        for voice in args.voices.split(","):
            for run in range(1, args.runs + 1):
                pcm = await speak(text, voice)
                m = measure(pcm)
                heard = await hear(pcm)
                m |= {"n": n, "voice": voice, "run": run, "units": u, "rate": u / m["speech"] if m["speech"] else 0}
                rows.append(m)
                print(f"  {voice:7} {run}  {m['duration']:5.2f} s  speech {m['speech']:5.2f}  pauses {m['pauses']:4.2f} "
                      f"(longest {m['longest']:4.2f})  lead {m['lead']:4.2f} tail {m['tail']:4.2f}  "
                      f"{m['rate']:4.1f} u/s  | {heard}")
                if out:
                    (out / f"{n:02d}-{voice}-{run}.pcm").write_bytes(pcm)
                sys.stdout.flush()

    print("\n== per sentence and voice: duration median, worst / median, rate median (units per second of speech)")
    for n, text in enumerate(SENTENCES, 1):
        for voice in args.voices.split(","):
            r = [x for x in rows if x["n"] == n and x["voice"] == voice]
            if not r:
                continue
            d = [x["duration"] for x in r]
            med = statistics.median(d)
            print(f"  [{n:2}] {voice:7} median {med:5.2f} s  worst {max(d):5.2f} s (x{max(d) / med:4.2f})  "
                  f"rate {statistics.median(x['rate'] for x in r):4.1f} u/s  longest pause {max(x['longest'] for x in r):4.2f} s")
    zh = [x["rate"] for x in rows if x["n"] <= 5 and x["rate"]] or [0]
    en = [x["rate"] for x in rows if 6 <= x["n"] <= 10 and x["rate"]] or [0]
    print(f"\n== speech rate, all clips: Chinese {statistics.median(zh):.1f} chars/s "
          f"(p10 {np.percentile(zh, 10):.1f}, p90 {np.percentile(zh, 90):.1f}); "
          f"English {statistics.median(en):.1f} syllables/s (p10 {np.percentile(en, 10):.1f}, p90 {np.percentile(en, 90):.1f})")
    if TIMES:
        print(f"== first audio median {statistics.median(t[0] for t in TIMES) * 1000:.0f} ms (p90 "
              f"{np.percentile([t[0] for t in TIMES], 90) * 1000:.0f}); real-time factor (made in / audio) median "
              f"{statistics.median(t[1] / t[2] for t in TIMES if t[2]):.2f}")
    over = [x for x in rows if x["duration"] > 1.4 * statistics.median(y["duration"] for y in rows if y["n"] == x["n"] and y["voice"] == x["voice"])]
    print(f"== clips over 1.4x their sentence's median: {len(over)} of {len(rows)}")


asyncio.run(main())
