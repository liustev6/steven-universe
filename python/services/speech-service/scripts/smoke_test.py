"""Smoke test against the running speech-service, as the `smoke-test` client: a sentence spoken, then
heard back.

Run by infra/deployment/deploy-target.sh (the Smoke test step) after every deploy, inside the new
image with host networking (no GPU: it's only a client), through DNS, port 443 and TLS, with the
smoke-test client's secret at /t/secret:

    docker run --rm --network host --read-only -v <this file>:/t/smoke.py:ro -v <secret>:/t/secret:ro \\
        --entrypoint python <image> /t/smoke.py

Checks: health; no token is refused (HTTP and a WebSocket handshake); smoke-test's token is accepted
(voices); errors are problem details; no status page without status:read; a sentence comes back as
24 kHz audio that isn't silence; that audio, streamed into a speech-to-text session, is heard as the
sentence; a TTS session gives its markers and `done`.
"""

import asyncio
import json
import re
import sys
from difflib import SequenceMatcher

import httpx
import numpy as np
import websockets
from scipy.signal import resample_poly
from service_auth import TokenClient

URL = "https://speech.lan.liustev6.ca"
WS = URL.replace("https", "wss", 1)
ISSUER = "https://auth.lan.liustev6.ca"
SENTENCE = "The kitchen lights are on, and the front door is locked."
failures = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global failures
    failures += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))


def alike(a: str, b: str) -> float:
    norm = lambda t: re.sub(r"[\W_]+", "", t.lower())  # noqa: E731
    return SequenceMatcher(None, norm(a), norm(b)).ratio()


async def main() -> int:
    tokens = TokenClient(ISSUER, "smoke-test", open("/t/secret").read().strip())
    token = await tokens.token("speech-service")
    auth = {"authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(base_url=URL, timeout=60) as http:
        r = await http.get("/health")
        check("health", r.status_code == 200 and r.json() == {"status": "ok"}, r.text[:100])
        r = await http.get("/v1/voices")
        check("no token: 401", r.status_code == 401, str(r.status_code))
        try:
            async with websockets.connect(f"{WS}/v1/realtime/speech?voice=ryan"):
                check("no token, WebSocket: refused at the handshake", False, "accepted")
        except websockets.exceptions.InvalidStatus as e:
            check("no token, WebSocket: refused at the handshake (401)", e.response.status_code == 401, str(e.response.status_code))

        r = await http.get("/v1/voices", headers=auth)
        voices = {v["name"] for v in r.json().get("voices", [])} if r.status_code == 200 else set()
        check("smoke-test's token accepted (voices)", "ryan" in voices, f"{r.status_code} {sorted(voices)}")
        r = await http.post("/v1/audio/speech", json={"input": "hi", "voice": "nobody"}, headers=auth)
        check("errors are problem details", r.status_code == 400 and r.headers.get("content-type") == "application/problem+json"
              and r.json().get("code") == "unknown_voice", f"{r.status_code} {r.text[:100]}")
        r = await http.get("/v1/status", headers=auth)
        check("no status page without status:read: 403", r.status_code == 403, str(r.status_code))

        r = await http.post("/v1/audio/speech", json={"input": SENTENCE, "voice": "ryan"}, headers=auth)
        audio = np.frombuffer(r.content, dtype="<i2").astype(np.float32) / 32768 if r.status_code == 200 else np.zeros(0)
        loud = float(np.sqrt(np.mean(audio ** 2))) if len(audio) else 0.0
        check("a sentence spoken: 24 kHz audio, not silence", r.headers.get("x-sample-rate") == "24000"
              and len(audio) > 24000 and loud > 0.01, f"{len(audio) / 24000:.1f} s, rms {loud:.3f}")

    if len(audio):
        heard = await hear(resample_poly(audio, 2, 3).astype(np.float32), auth)
        check("heard back: the same sentence", alike(heard, SENTENCE) > 0.85, repr(heard))

    async with websockets.connect(f"{WS}/v1/realtime/speech?voice=ryan", additional_headers=auth) as ws:
        msgs = [json.loads(await ws.recv())]
        await ws.send(json.dumps({"type": "text", "text": "Sure. All set."}))
        await ws.send(json.dumps({"type": "end"}))
        samples = 0
        async for m in ws:
            if isinstance(m, bytes):
                samples += len(m) // 2
            else:
                msgs.append(json.loads(m))
                if msgs[-1]["type"] in ("done", "error"):
                    break
        kinds = [m["type"] for m in msgs]
        check("a TTS session: started, sentence markers, one reply's audio, done",
              kinds[0] == "started" and msgs[0].get("sample_rate") == 24000 and "sentence" in kinds
              and kinds[-1] == "done" and msgs[-1].get("samples") == samples > 0, f"{kinds}, {samples / 24000:.1f} s")
    await tokens.aclose()
    print("ALL PASSED" if not failures else f"{failures} FAILED")
    return 1 if failures else 0


async def hear(audio16: np.ndarray, auth: dict) -> str:
    """An STT session: the audio in 100 ms frames (as fast as it goes), then the commit; the final."""
    async with websockets.connect(f"{WS}/v1/realtime/transcription", additional_headers=auth) as ws:
        msg = json.loads(await ws.recv())
        while msg["type"] == "queued":
            msg = json.loads(await ws.recv())
        for i in range(0, len(audio16), 1600):
            await ws.send((np.clip(audio16[i:i + 1600], -1, 1) * 32767).astype("<i2").tobytes())
        await ws.send(json.dumps({"type": "commit"}))
        async for m in ws:
            msg = json.loads(m)
            if msg["type"] in ("final", "error"):
                return msg.get("text", f"error: {msg.get('code')}")
    return ""


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
