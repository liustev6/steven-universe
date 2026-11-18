"""The two WebSocket sessions (speech-service.md T13, the API table). A session is one connection:
a TTS session speaks one reply, a speech-to-text session hears one utterance; then it closes.

Both, in order:
  1. the handshake: the token (speech:create, or speech:transcribe), the voice or language, and a place
     (admission: 2 running + 1 waiting); refused here with an HTTP status and a problem body
     (401/403, 400, 503 + Retry-After), before the connection opens
  2. accepted: `queued` with its position if it must wait (10 s at most, then `error` queue_timeout),
     then `started`
  3. the session: messages in, results out, until its end, a cancel, a disconnect, idle, or too long
  4. its place is released, however it ended

The sessions never touch the GPU: they hand work to a work thread (app/work) and get results back
through an asyncio queue (the worker's sink puts into it from its thread, call_soon_threadsafe).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections import deque

import numpy as np
from fastapi import HTTPException, WebSocket, WebSocketDisconnect

from app.api.errors import problem
from app.service import Service
from app.work.admission import QueueTimeout, Refused
from app.work.sentences import Sentences

logger = logging.getLogger(__name__)
TTS_RATE, STT_RATE = 24000, 16000


def pcm16(audio: np.ndarray) -> bytes:
    """float32 [-1, 1] → 16-bit little-endian PCM."""
    return (np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()


async def authenticate(ws: WebSocket, auth, scope: str):
    """The caller's Principal, or None after refusing the handshake (401/403/503)."""
    try:
        principal = await auth.authenticate(ws)
    except HTTPException as exc:
        await ws.send_denial_response(problem(exc.status_code, "unauthorized" if exc.status_code == 401 else "auth_unavailable",
                                              exc.detail, headers=exc.headers))
        return None
    if not principal.has_scopes(scope):
        await ws.send_denial_response(problem(403, "insufficient_scope", f"needs {scope}",
                                              headers={"WWW-Authenticate": f'Bearer error="insufficient_scope", scope="{scope}"'}))
        return None
    return principal


async def admit(ws: WebSocket, service: Service, admission):
    """A ticket (running or waiting), or None after refusing the handshake (503: warming, or full)."""
    retry = {"Retry-After": str(service.settings.RETRY_AFTER_SECONDS)}
    if not service.warm:
        await ws.send_denial_response(problem(503, "warming_up", "the models are still loading", headers=retry))
        return None
    try:
        return admission.enter()
    except Refused as exc:
        await ws.send_denial_response(problem(503, "busy", str(exc), headers=retry))
        return None


async def wait_for_place(ws: WebSocket, ticket, service: Service) -> bool:
    """`queued`, then `started`; False (after an error message) if the wait ran out."""
    if not ticket.running:
        await ws.send_json({"type": "queued", "position": ticket.position})
        try:
            await ticket.started(service.settings.QUEUE_WAIT_SECONDS)
        except QueueTimeout:
            await ws.send_json({"type": "error", "code": "queue_timeout", "message": "no place in time"})
            return False
    await ws.send_json({"type": "started"})
    return True


class Inbox:
    """One asyncio queue for both directions' events: the client's messages (a reader task) and the
    work thread's results (its sink, from another thread)."""

    def __init__(self):
        self.queue: asyncio.Queue = asyncio.Queue()
        self.loop = asyncio.get_running_loop()

    def sink(self, kind: str, payload) -> None:                    # called from a work thread
        self.loop.call_soon_threadsafe(self.queue.put_nowait, ("work", kind, payload))

    async def read(self, ws: WebSocket) -> None:                   # the reader task
        try:
            while True:
                msg = await ws.receive()
                if msg["type"] == "websocket.disconnect":
                    break
                self.queue.put_nowait(("client", "bytes" if msg.get("bytes") is not None else "text",
                                       msg.get("bytes") if msg.get("bytes") is not None else msg.get("text")))
        finally:
            self.queue.put_nowait(("client", "gone", None))

    async def next(self, timeout: float):
        try:
            return await asyncio.wait_for(self.queue.get(), max(0.0, timeout))
        except TimeoutError:
            return ("timeout", None, None)


async def close(ws: WebSocket, error: str | None = None, message: str = "") -> None:
    with contextlib.suppress(Exception):
        if error:
            await ws.send_json({"type": "error", "code": error, "message": message})
        await ws.close()


def control(raw) -> dict:
    """A client's JSON message, or {} if it isn't one."""
    try:
        msg = json.loads(raw)
        return msg if isinstance(msg, dict) else {}
    except (TypeError, ValueError):
        return {}


# ---- TTS: one reply ----------------------------------------------------------------------------------
async def speech_session(ws: WebSocket, service: Service, auth) -> None:
    """Client: {type: text, text} (as it's written), {type: end}, {type: cancel}. Server: queued, started,
    binary audio (24 kHz 16-bit PCM), done, error (idle, too_long, queue_timeout, bad_message, failed)."""
    s = service.settings
    voice, language = ws.query_params.get("voice", ""), ws.query_params.get("language") or None
    if await authenticate(ws, auth, "speech:create") is None:
        return
    if voice not in service.voices:
        await ws.send_denial_response(problem(400, "unknown_voice", f"voices: {', '.join(sorted(service.voices))}"))
        return
    if (ticket := await admit(ws, service, service.tts_admission)) is None:
        return
    await ws.accept()
    service.sessions["tts"] += 1
    inbox, reader, job = Inbox(), None, None
    try:
        if not await wait_for_place(ws, ticket, service):
            return await close(ws)
        reader = asyncio.create_task(inbox.read(ws))
        sentences, pending = Sentences(), deque()
        ended, chars, start = False, 0, time.monotonic()
        idle_since, first_audio_at, asked_at = time.monotonic(), None, None
        while True:
            if job is None and pending:                            # the next sentence, when the last is done
                job = service.tts.say(pending.popleft(), voice, language, inbox.sink)
                asked_at = asked_at or time.monotonic()
            if job is None and ended and not pending:
                await ws.send_json({"type": "done"})
                return await close(ws)
            limit = start + s.TTS_MAX_SECONDS
            idle = idle_since + s.TTS_IDLE_SECONDS if job is None and not pending and not ended else limit
            source, kind, payload = await inbox.next(min(limit, idle) - time.monotonic())
            if source == "timeout":
                return await close(ws, "too_long" if time.monotonic() >= limit else "idle",
                                   "the reply ran past its limit" if time.monotonic() >= limit else "no text for a while")
            if source == "client":
                if kind == "gone":
                    return
                msg = control(payload)
                if msg.get("type") == "text" and isinstance(msg.get("text"), str) and not ended:
                    chars += len(msg["text"])
                    if chars > s.MAX_TEXT_CHARS:
                        return await close(ws, "too_long", f"over {s.MAX_TEXT_CHARS} characters")
                    pending.extend(sentences.add(msg["text"]))
                    idle_since = time.monotonic()
                elif msg.get("type") == "end":
                    pending.extend(sentences.flush())
                    ended = True
                elif msg.get("type") == "cancel":
                    return await close(ws)
                else:
                    return await close(ws, "bad_message", "expected {type: text|end|cancel}")
            elif kind == "audio":
                if first_audio_at is None:
                    first_audio_at = time.monotonic()
                    service.first_audio_ms.append(round((first_audio_at - asked_at) * 1000))
                await ws.send_bytes(pcm16(payload))
            elif kind == "done":
                job, idle_since = None, time.monotonic()
            elif kind == "error":
                job = None
                return await close(ws, "failed", str(payload))
    except WebSocketDisconnect:
        pass
    finally:
        if job is not None:
            service.tts.cancel(job)
        if reader is not None:
            reader.cancel()
        ticket.release()


# ---- STT: one utterance -----------------------------------------------------------------------------
async def transcription_session(ws: WebSocket, service: Service, auth) -> None:
    """Client: binary audio (16 kHz 16-bit PCM, ~100 ms each), {type: commit} (end of speech),
    {type: cancel}. Server: queued, started, {type: partial, text, language}, {type: final, text,
    language}, error (idle, too_long, queue_timeout, bad_message, failed)."""
    s = service.settings
    language = ws.query_params.get("language") or None
    if await authenticate(ws, auth, "speech:transcribe") is None:
        return
    if language and language not in service.languages:
        await ws.send_denial_response(problem(400, "unknown_language", f"languages: {', '.join(service.languages)}"))
        return
    if (ticket := await admit(ws, service, service.stt_admission)) is None:
        return
    await ws.accept()
    service.sessions["stt"] += 1
    inbox, reader, utterance, committed = Inbox(), None, None, None
    try:
        if not await wait_for_place(ws, ticket, service):
            return await close(ws)
        reader = asyncio.create_task(inbox.read(ws))
        utterance = service.stt.open(language, inbox.sink)
        start, last_audio = time.monotonic(), time.monotonic()
        while True:
            limit = start + s.STT_MAX_SECONDS + 5.0                 # the worker enforces the audio's 60 s
            idle = last_audio + s.STT_IDLE_SECONDS if committed is None else limit
            source, kind, payload = await inbox.next(min(limit, idle) - time.monotonic())
            if source == "timeout":
                return await close(ws, "idle" if time.monotonic() < limit else "too_long", "no audio for a while")
            if source == "client":
                if kind == "gone":
                    return
                if kind == "bytes":
                    if committed is not None or len(payload) % 2:
                        return await close(ws, "bad_message", "16-bit PCM frames, before the commit")
                    service.stt.audio(utterance, np.frombuffer(payload, dtype="<i2"))
                    last_audio = time.monotonic()
                    continue
                msg = control(payload)
                if msg.get("type") == "commit" and committed is None:
                    committed = time.monotonic()
                    service.stt.end(utterance)
                elif msg.get("type") == "cancel":
                    return await close(ws)
                else:
                    return await close(ws, "bad_message", "expected audio, {type: commit} or {type: cancel}")
            elif kind in ("partial", "final"):
                await ws.send_json({"type": kind, "text": payload.text, "language": payload.language})
                if kind == "final":
                    utterance = None
                    if committed is not None:
                        service.final_ms.append(round((time.monotonic() - committed) * 1000))
                    return await close(ws)
            elif kind == "error":
                utterance = None
                return await close(ws, "too_long" if payload == "too_long" else "failed", str(payload))
    except WebSocketDisconnect:
        pass
    finally:
        if utterance is not None:
            service.stt.cancel(utterance)
        if reader is not None:
            reader.cancel()
        ticket.release()
