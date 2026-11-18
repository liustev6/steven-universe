"""speech-service's two sessions (docs/specs/speech-service.md, API): one utterance heard, one reply
spoken. Each is one WebSocket, with pi-server's token (audience speech-service)."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import AsyncIterator, Awaitable, Callable, Optional
from urllib.parse import urlencode

import websockets

logger = logging.getLogger(__name__)


CONTEXT_CHARS = 1000                     # speech-service's limit for a session's context


class SpeechError(Exception):
    pass


@dataclass
class Heard:
    text: str
    language: str
    reason: str                  # silence | commit | too_long | idle


class SpeechClient:
    def __init__(self, url: str, token: Callable[[], Awaitable[str]]):
        self.ws_url = url.replace("https://", "wss://", 1).replace("http://", "ws://", 1)
        self.token = token

    async def _connect(self, kind: str, params: dict[str, str]):
        try:
            return await websockets.connect(
                f"{self.ws_url}/v1/realtime/{kind}?{urlencode(params)}",
                additional_headers={"Authorization": f"Bearer {await self.token()}"}, max_size=None)
        except websockets.exceptions.InvalidStatus as e:
            raise SpeechError(f"{kind}: refused, {e.response.status_code}") from e

    async def transcribe(self, frames: AsyncIterator[bytes], *, commit: asyncio.Event,
                         silence_ms: int, language: Optional[str] = None, context: str = "",
                         on_partial: Optional[Callable[[str], Awaitable[None]]] = None) -> Heard:
        """Send the mic's frames until the turn ends: the server hears the silence, or `commit` is set
        (push-to-talk released). Returns the final text. `context`: what the speech is likely about
        (speech-service T18: it biases the words and the language toward it)."""
        params = {"turn_detection": "server", "silence_ms": str(silence_ms)}
        if language:
            params["language"] = language
        if context:
            params["context"] = context[-CONTEXT_CHARS:]
        ws = await self._connect("transcription", params)
        try:
            async def send() -> None:
                async for frame in frames:
                    if commit.is_set():
                        await ws.send(json.dumps({"type": "commit"}))
                        return
                    await ws.send(frame)

            sender = asyncio.create_task(send())
            try:
                async for message in ws:
                    event = json.loads(message)
                    if event["type"] == "partial" and on_partial:
                        await on_partial(event["text"])
                    elif event["type"] == "final":
                        return Heard(event["text"], event.get("language", ""), event.get("reason", ""))
                    elif event["type"] == "error":
                        raise SpeechError(f"transcription: {event.get('code')}: {event.get('message')}")
                if sender.done() and sender.exception():
                    raise sender.exception()
                raise SpeechError(f"transcription closed before its final ({ws.close_code} {ws.close_reason})")
            finally:
                sender.cancel()
                await asyncio.wait([sender])          # ended, so the caller can close the mic's frames
        finally:
            await ws.close()

    async def speak(self, texts: AsyncIterator[str], *, voice: str, language: Optional[str],
                    on_audio: Callable[[bytes], Awaitable[None]]) -> None:
        """Stream a reply's text in as it's written; each audio packet to `on_audio` as it comes."""
        params = {"voice": voice} | ({"language": language} if language else {})
        ws = await self._connect("speech", params)
        try:
            started = asyncio.Event()

            async def feed() -> None:
                await started.wait()
                async for text in texts:
                    await ws.send(json.dumps({"type": "text", "text": text}))
                await ws.send(json.dumps({"type": "end"}))

            feeder = asyncio.create_task(feed())
            try:
                async for message in ws:
                    if isinstance(message, bytes):
                        await on_audio(message)
                        continue
                    event = json.loads(message)
                    if event["type"] == "started":
                        started.set()
                    elif event["type"] == "done":
                        return
                    elif event["type"] == "error":
                        raise SpeechError(f"speech: {event.get('code')}: {event.get('message')}")
                if feeder.done() and feeder.exception():
                    raise feeder.exception()
                raise SpeechError(f"speech closed before done ({ws.close_code} {ws.close_reason})")
            finally:
                feeder.cancel()
                await asyncio.wait([feeder])
        finally:
            await ws.close()
