"""The HTTP routes (speech-service.md, the API table)."""

from __future__ import annotations

import asyncio
import struct
import time
import uuid
from typing import Literal

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from app.api.errors import problem
from app.api.sessions import TTS_RATE, Inbox, pcm16
from app.service import Service
from app.work.admission import QueueTimeout, Refused
from app.work.sentences import Sentences


class SpeechRequest(BaseModel):
    input: str = Field(min_length=1)
    voice: str
    language: str | None = None
    response_format: Literal["pcm", "wav"] = "pcm"


def wav_header(rate: int) -> bytes:
    """A WAV header for a stream of unknown length (sizes 0xFFFFFFFF, as streaming servers send)."""
    return (b"RIFF" + struct.pack("<I", 0xFFFFFFFF) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
            + b"data" + struct.pack("<I", 0xFFFFFFFF))


def router(service: Service, auth) -> APIRouter:
    r, s = APIRouter(), service.settings
    busy = {"Retry-After": str(s.RETRY_AFTER_SECONDS)}

    @r.post("/v1/audio/speech", dependencies=[Depends(auth.require("speech:create"))])
    async def speech(req: SpeechRequest):
        """A one-shot TTS session (smoke tests, simple callers): the whole input, audio streamed back."""
        if req.voice not in service.voices:
            return problem(400, "unknown_voice", f"voices: {', '.join(sorted(service.voices))}")
        if len(req.input) > s.MAX_TEXT_CHARS:
            return problem(400, "too_long", f"over {s.MAX_TEXT_CHARS} characters")
        if not service.warm:
            return problem(503, "warming_up", "the models are still loading", headers=busy)
        try:
            ticket = service.tts_admission.enter()
        except Refused as exc:
            return problem(503, "busy", str(exc), headers=busy)
        try:
            await ticket.started(s.QUEUE_WAIT_SECONDS)
        except QueueTimeout:
            return problem(503, "busy", "no place in time", headers=busy)
        service.sessions["tts"] += 1
        sentences = Sentences(first_clause=s.TTS_FIRST_CLAUSE)
        parts = sentences.add(req.input) + sentences.flush()

        async def body():
            inbox, job, first = Inbox(), None, None
            asked = time.monotonic()
            try:
                if req.response_format == "wav":
                    yield wav_header(TTS_RATE)
                job = service.tts.open(req.voice, req.language, inbox.sink)    # one generation, the whole reply (T19)
                for sentence in parts:
                    service.tts.feed(job, sentence)
                service.tts.end(job)
                while True:
                    _, kind, payload = await inbox.next(s.TTS_MAX_SECONDS)
                    if kind == "audio":
                        if first is None:
                            first = time.monotonic()
                            service.first_audio_ms.append(round((first - asked) * 1000))
                        yield pcm16(payload)
                    elif kind == "done":
                        job = None
                        return
                    else:                                                  # an error, or the limit
                        return
            finally:                                                       # done, failed, or the client left
                if job is not None:
                    service.tts.cancel(job)
                ticket.release()

        return StreamingResponse(body(), media_type="audio/wav" if req.response_format == "wav" else "audio/pcm",
                                 headers={"X-Sample-Rate": str(TTS_RATE), "X-Request-Id": uuid.uuid4().hex})

    @r.get("/v1/voices", dependencies=[Depends(auth.require("speech:create"))])
    async def voices():
        return {"voices": [{"name": n, "language": lang} for n, lang in sorted(service.voices.items())]}

    @r.get("/v1/status", dependencies=[Depends(auth.require("status:read"))])
    async def status():
        return await asyncio.to_thread(service.status)

    @r.get("/health")
    async def health():
        if not service.warm:
            return JSONResponse({"status": "warming_up"}, status_code=503)
        return {"status": "ok"}

    return r
