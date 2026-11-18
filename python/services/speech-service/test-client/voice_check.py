"""The voice loop, checked without a mic: a question is spoken (speech-service, through the test client),
then played into the test client's /voice in real time, as a person would say it; the reply is heard,
its text printed, and each hop timed.

    cd python/services/speech-service
    .venv/bin/python test-client/server.py &                 # the test client, on localhost:8765
    .venv/bin/python test-client/voice_check.py "What's the tallest mountain in Canada?"

Times are from the transcript's final (the turn "heard"); the silence that ended the turn comes before it.
The reply's audio is saved to --out (a WAV) to listen to.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
import wave

import numpy as np
import websockets

ap = argparse.ArgumentParser()
ap.add_argument("question", nargs="*", default=["What's", "the", "tallest", "mountain", "in", "Canada?"])
ap.add_argument("--client", default="ws://localhost:8765")
ap.add_argument("--voice", default="ryan")
ap.add_argument("--asker", default="serena", help="the voice that asks")
ap.add_argument("--silence-ms", type=int, default=700)
ap.add_argument("--turns", type=int, default=1, help="ask it this many times, in one conversation")
ap.add_argument("--out", default="voice-reply.wav")
args = ap.parse_args()


async def spoken(text: str) -> np.ndarray:
    """The question as 16 kHz speech (made at 24 kHz, resampled)."""
    pcm = bytearray()
    async with websockets.connect(f"{args.client}/v1/realtime/speech?voice={args.asker}", max_size=None) as ws:
        async for m in ws:
            if isinstance(m, bytes):
                pcm += m
                continue
            e = json.loads(m)
            if e["type"] == "started":
                await ws.send(json.dumps({"type": "text", "text": text}))
                await ws.send(json.dumps({"type": "end"}))
            if e["type"] == "error":
                raise SystemExit(f"speaking the question: {e}")
    x = np.frombuffer(bytes(pcm), np.int16).astype(np.float32)
    t = np.arange(0, len(x) * 2 // 3) * 1.5
    return np.interp(t, np.arange(len(x)), x).astype(np.int16)


async def main():
    text = " ".join(args.question)
    question = await spoken(text)
    print(f"question: {text!r}  ({len(question) / 16000:.1f} s of speech, voice {args.asker})")
    silence = np.zeros(16000 * 3, np.int16)                            # after it: quiet, until the turn ends
    reply = bytearray()
    async with websockets.connect(f"{args.client}/voice?voice={args.voice}&silence_ms={args.silence_ms}",
                                  max_size=None) as ws:
        for turn in range(args.turns):
            await ws.send(json.dumps({"type": "listen"}))
            audio = np.concatenate([question, silence])
            stop = asyncio.Event()

            async def talk():
                start = time.monotonic()
                for i in range(0, len(audio), 1600):                  # 100 ms frames, in real time
                    if stop.is_set():
                        return
                    await asyncio.sleep(max(0, start + i / 16000 - time.monotonic()))
                    await ws.send(audio[i:i + 1600].tobytes())

            talker = asyncio.create_task(talk())
            spoke_end = time.monotonic() + len(question) / 16000
            heard_at = None
            words = []
            async for m in ws:
                if isinstance(m, bytes):
                    reply += m
                    continue
                e = json.loads(m)
                if e.get("type") == "agent_session":
                    print(f"agent session {e['session_id'][:8]} ({e['state']}) in {e['took_ms']} ms")
                elif e.get("from") == "stt" and e["type"] == "final":
                    heard_at = time.monotonic()
                    stop.set()
                    print(f"heard ({e['reason']}, {round((heard_at - spoke_end) * 1000)} ms after the speech ended): {e['text']!r}")
                elif e.get("from") == "agent" and e["type"] == "message.delta":
                    words.append(e["text"])
                elif e.get("type") == "error":
                    print("ERROR", e)
                elif e.get("type") == "reply_done":
                    print(f"reply: {''.join(words).strip()!r}")
                    print(f"timings (ms from heard): {e['timings']}")
                    break
            talker.cancel()
            if turn + 1 < args.turns:
                await asyncio.sleep(len(reply) / 2 / 24000)              # as if it played
    with wave.open(args.out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(bytes(reply))
    print(f"reply audio: {len(reply) / 2 / 24000:.1f} s → {args.out}")


asyncio.run(main())
