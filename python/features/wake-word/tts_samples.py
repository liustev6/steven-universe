"""Wake-word samples from speech-service's own voices (the live service, through the test client):
positives ("Hey Pi" said several ways) and near-miss negatives (what sounds close and must not wake it).
Piper's voices, which the training notebook generates, are all American English; these add our 9
voices, Chinese-accented ones included, as the people around the Pi sound.

    cd python/services/speech-service && .venv/bin/python test-client/server.py &
    python tts_samples.py data/positives/tts data/negatives/tts --runs 5

16 kHz 16-bit mono WAVs (the notebook's format), the silence trimmed to 150 ms at each end.
"""

import argparse
import asyncio
import json
import wave
from pathlib import Path

import numpy as np
import websockets

POSITIVE = ["Hey Pi.", "Hey Pi!", "Hey Pi?", "Hey, Pi.", "嘿，Pi。", "Hey Pie."]
NEAR_MISS = ["Hey.", "Pie.", "Happy.", "Hey buddy.", "Hey guys.", "Bye.", "Hi.", "Hey, hi.", "Okay.",
             "Apple pie.", "Hey pal.", "Hey Bai.", "嗨。", "拜拜。", "派。", "嘿。", "好的。", "嗨，你好。"]

ap = argparse.ArgumentParser()
ap.add_argument("positives")
ap.add_argument("negatives")
ap.add_argument("--client", default="ws://localhost:8765")
ap.add_argument("--voices", default="ryan,aiden,dylan,eric,serena,vivian,uncle_fu,ono_anna,sohee")
ap.add_argument("--runs", type=int, default=5)
args = ap.parse_args()


async def speak(text: str, voice: str) -> np.ndarray:
    pcm = bytearray()
    async with websockets.connect(f"{args.client}/v1/realtime/speech?voice={voice}", max_size=None) as ws:
        async for m in ws:
            if isinstance(m, bytes):
                pcm += m
                continue
            e = json.loads(m)
            if e["type"] == "started":
                await ws.send(json.dumps({"type": "text", "text": text}))
                await ws.send(json.dumps({"type": "end"}))
            if e["type"] in ("done", "error"):
                break
    x = np.frombuffer(bytes(pcm), np.int16).astype(np.float32)
    x16 = np.interp(np.arange(0, len(x) * 2 // 3) * 1.5, np.arange(len(x)), x)       # 24 → 16 kHz
    loud = np.flatnonzero(np.abs(x16) > 32768 * 10 ** (-45 / 20))
    if len(loud):
        x16 = x16[max(0, loud[0] - 2400): loud[-1] + 2400]                            # 150 ms each side
    return x16.astype(np.int16)


def save(path: Path, x: np.ndarray) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(16000), w.writeframes(x.tobytes())


async def main():
    for folder, phrases in ((Path(args.positives), POSITIVE), (Path(args.negatives), NEAR_MISS)):
        folder.mkdir(parents=True, exist_ok=True)
        n = 0
        for voice in args.voices.split(","):
            for i, text in enumerate(phrases):
                for run in range(args.runs):
                    x = await speak(text, voice)
                    save(folder / f"{voice}-{i:02d}-{run}.wav", x)
                    n += 1
        print(f"{n} clips → {folder}")


asyncio.run(main())
