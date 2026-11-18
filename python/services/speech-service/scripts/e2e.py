"""End to end against a running speech-service (4.5f step 3): real sessions over the network.

    python scripts/e2e.py http://127.0.0.1:8443 [--token TOKEN]

  1. /health until warm (the time it took)
  2. a TTS session: a reply's text in pieces; first audio (from the first text), done; the audio kept
  3. an STT session: that audio, 16 kHz, 100 ms at a time in real time; partials; the final after the
     commit (the time); how alike it is to the text
  4. 2 + 2 at once (two replies, two utterances), then a third TTS session (must wait: queued) and a
     fourth (must be refused: 503)
  5. POST /v1/audio/speech (WAV), and /v1/status
"""

import asyncio
import json
import re
import sys
import time
from difflib import SequenceMatcher

import httpx
import numpy as np
import websockets

BASE = sys.argv[1].rstrip("/")
WS = BASE.replace("http", "ws", 1)
TOKEN = sys.argv[sys.argv.index("--token") + 1] if "--token" in sys.argv else "dev"
HEADERS = {"authorization": f"Bearer {TOKEN}"}
REPLY = ["Sure. Tomorrow looks ", "cloudy in the morning, ", "with a little rain ", "in the afternoon."]
same = lambda a, b: SequenceMatcher(None, *(re.sub(r"[\W_]+", "", t.lower()) for t in (a, b))).ratio()  # noqa: E731


async def speak(pieces, voice="aiden", *, gap=0.15):
    """A TTS session: (first audio ms, total s, audio float32 24 kHz, messages)."""
    async with websockets.connect(f"{WS}/v1/realtime/speech?voice={voice}", additional_headers=HEADERS) as ws:
        msgs = [json.loads(await ws.recv())]
        while msgs[-1]["type"] == "queued":
            msgs.append(json.loads(await ws.recv()))
        start, first, audio = time.perf_counter(), None, []

        async def send():
            for p in pieces:
                await ws.send(json.dumps({"type": "text", "text": p}))
                await asyncio.sleep(gap)                          # as an agent writes it
            await ws.send(json.dumps({"type": "end"}))

        sender = asyncio.create_task(send())
        async for m in ws:
            if isinstance(m, bytes):
                first = first or time.perf_counter() - start
                audio.append(np.frombuffer(m, dtype="<i2").astype(np.float32) / 32768)
            else:
                msgs.append(json.loads(m))
                if msgs[-1]["type"] in ("done", "error", "cancelled"):
                    break
        await sender
        return (first or 0) * 1000, time.perf_counter() - start, np.concatenate(audio) if audio else np.zeros(0), msgs


async def hear(audio16):
    """An STT session, the audio in real time: (final ms after the commit, final text, partials, messages)."""
    async with websockets.connect(f"{WS}/v1/realtime/transcription", additional_headers=HEADERS) as ws:
        msgs = [json.loads(await ws.recv())]
        while msgs[-1]["type"] == "queued":
            msgs.append(json.loads(await ws.recv()))
        partials, step = [], 1600

        async def send():
            start = time.perf_counter()
            for k, off in enumerate(range(0, len(audio16), step)):
                await asyncio.sleep(max(0.0, start + off / 16000 - time.perf_counter()))
                await ws.send((np.clip(audio16[off:off + step], -1, 1) * 32767).astype("<i2").tobytes())
            await asyncio.sleep(max(0.0, start + len(audio16) / 16000 - time.perf_counter()))
            await ws.send(json.dumps({"type": "commit"}))
            return time.perf_counter()

        sender = asyncio.create_task(send())
        async for m in ws:
            msg = json.loads(m)
            msgs.append(msg)
            if msg["type"] == "partial":
                partials.append(msg["text"])
            if msg["type"] in ("final", "error"):
                break
        committed = await sender
        return (time.perf_counter() - committed) * 1000, msgs[-1].get("text", ""), partials, msgs


def to16k(audio24):
    from scipy.signal import resample_poly
    return resample_poly(audio24, 2, 3).astype(np.float32)


async def main():
    t = time.perf_counter()
    async with httpx.AsyncClient(timeout=10) as http:
        while True:                                           # not listening yet, then 503 until warm
            try:
                if (await http.get(f"{BASE}/health")).status_code == 200:
                    break
            except httpx.TransportError:
                pass
            await asyncio.sleep(0.5)
    print(f"1. warm after {time.perf_counter() - t:.1f} s of waiting")

    first, total, audio, msgs = await speak(REPLY)
    marks = [m for m in msgs if m["type"] in ("sentence", "sentence_done")]
    print(f"2. TTS: {msgs[0]}; first audio {first:.0f} ms, {len(audio) / 24000:.1f} s of speech in {total:.1f} s; "
          f"{len(marks) // 2} sentences ({', '.join(str(m['samples']) for m in marks if m['type'] == 'sentence_done')} samples); "
          f"ends {msgs[-1]['type']}")
    import soundfile as sf
    sf.write("/tmp/e2e-reply.wav", audio, 24000)

    final_ms, text, partials, msgs = await hear(to16k(audio))
    last = [m for m in msgs if m["type"] == "partial"][-1]
    print(f"3. STT: {len(partials)} partials (the last: stable {last['stable']!r} of {last['text']!r}); final {final_ms:.0f} ms "
          f"after the commit, reason {msgs[-1].get('reason')}: {text!r} ({same(text, ''.join(REPLY)):.2f} alike)")

    zh = ["好的，", "我现在帮你", "把客厅的灯关掉。"]
    results = await asyncio.gather(speak(REPLY, "aiden"), speak(zh, "vivian"), hear(to16k(audio)), hear(to16k(audio)))
    print("4. 2 + 2 at once: TTS first audio "
          + ", ".join(f"{r[0]:.0f} ms ({r[3][-1]['type']})" for r in results[:2])
          + "; STT finals " + ", ".join(f"{r[0]:.0f} ms ({same(r[1], ''.join(REPLY)):.2f})" for r in results[2:]))

    async def third_and_fourth():
        long = ["This reply is long enough to keep its place for a while. "] * 6
        a, b = asyncio.create_task(speak(long)), asyncio.create_task(speak(long))
        await asyncio.sleep(0.5)
        c = asyncio.create_task(speak(["Short."]))
        await asyncio.sleep(0.3)
        try:
            async with websockets.connect(f"{WS}/v1/realtime/speech?voice=ryan", additional_headers=HEADERS):
                refused = "accepted (unexpected)"
        except websockets.exceptions.InvalidStatus as e:
            refused = f"{e.response.status_code}, Retry-After {e.response.headers.get('Retry-After')}"
        res = await asyncio.gather(a, b, c)
        return res[2][3][0], refused

    queued, refused = await third_and_fourth()
    print(f"   a third TTS session: {queued}; a fourth: {refused}")

    async with httpx.AsyncClient(timeout=60, headers=HEADERS) as http:
        r = await http.post(f"{BASE}/v1/audio/speech", json={"input": "The kitchen lights are on.", "voice": "ryan",
                                                             "response_format": "wav"})
        print(f"5. POST /v1/audio/speech: {r.status_code}, {r.headers.get('content-type')}, {(len(r.content) - 44) / 48000:.1f} s")
        print("   status:", json.dumps((await http.get(f"{BASE}/v1/status")).json()))


asyncio.run(main())
