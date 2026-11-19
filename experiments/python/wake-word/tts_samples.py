"""Wake-word samples from speech-service's own voices (the live service, through its test client): one kind of
phrase from a phrase set (phrases/*.yaml: `positive`, `near_miss`, `speech`), said by each voice, `--runs`
times each. The TTS samples, so every run is a different take. Piper's voices, which the training notebook
generates, are all American English; these are our 9 voices, Chinese-accented ones included, as the people
around the Pi sound, and the only source for a Chinese wake word.

    cd python/services/speech-service && .venv/bin/python test-client/server.py &
    python tts_samples.py phrases/hey_pi.yaml positive data/datasets/processed/train/heypi-tts --runs 5
    python tts_samples.py phrases/hey_pi.yaml near_miss data/datasets/processed/train/heypi-tts-near --runs 5

16 kHz 16-bit mono WAVs (the notebook's format), the silence trimmed to 150 ms at each end; named
`{voice}-{phrase}-{run}.wav`, and a file already there is kept (a rerun resumes).
"""

import argparse
import asyncio
import json
import wave
from pathlib import Path

import numpy as np
import websockets
import yaml

VOICES = "ryan,aiden,dylan,eric,serena,vivian,uncle_fu,ono_anna,sohee"

ap = argparse.ArgumentParser()
ap.add_argument("phrases", help="a phrase set: phrases/*.yaml")
ap.add_argument("kind", help="which of its lists: positive, near_miss, speech")
ap.add_argument("out")
ap.add_argument("--client", default="ws://localhost:8765")
ap.add_argument("--voices", default=VOICES)
ap.add_argument("--runs", type=int, default=5)
ap.add_argument("--concurrency", type=int, default=2, help="speech-service runs 2 at a time")
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
                if e["type"] == "error":
                    raise RuntimeError(f"{voice} {text!r}: {e}")
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
    phrases = yaml.safe_load(open(args.phrases))[args.kind]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    jobs = [(voice, i, text, run) for voice in args.voices.split(",") for i, text in enumerate(phrases)
            for run in range(args.runs)]
    todo = [j for j in jobs if not (out / f"{j[0]}-{j[1]:02d}-{j[3]}.wav").exists()]
    print(f"{len(jobs)} clips → {out} ({len(jobs) - len(todo)} already there)", flush=True)
    queue: asyncio.Queue = asyncio.Queue()
    for j in todo:
        queue.put_nowait(j)
    done = 0

    async def worker():
        nonlocal done
        while not queue.empty():
            voice, i, text, run = queue.get_nowait()
            for attempt in range(3):
                try:
                    save(out / f"{voice}-{i:02d}-{run}.wav", await speak(text, voice))
                    break
                except Exception as e:                       # the service busy (503), a dropped socket: again
                    if attempt == 2:
                        print(f"   gave up: {voice} {text!r}: {e}", flush=True)
                    await asyncio.sleep(2)
            done += 1
            if done % 100 == 0:
                print(f"   {done}/{len(todo)}", flush=True)

    await asyncio.gather(*(worker() for _ in range(args.concurrency)))


asyncio.run(main())
