"""speech-service's process (speech-service.md T3, T12, T13). Start-up, in order:

    1. the API listens at once: /health says 503 (warming up) and sessions are refused with 503
    2. a background thread loads both models (the TTS: our fused predictor and graphed talker; the STT:
       Qwen3-ASR), builds their batches and work threads, and warms them THROUGH the work threads: a
       sentence spoken and an utterance heard, which captures the batches' CUDA graphs
    3. warm: /health says 200, sessions are admitted

If loading fails, the process exits (non-zero): Docker restarts it and the health check never passed.
On SIGTERM, uvicorn stops accepting, gives open sessions 5 s, then the work threads stop.

    python -m app.main      (settings: SPEECH_*, app/config.py; auth: SERVICE_AUTH_ISSUER)
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time

import numpy as np
import uvicorn

from app.api import AUDIENCE, create_api
from app.config import Settings
from app.service import Service

logger = logging.getLogger("app.main")


def load(service: Service, settings: Settings) -> None:
    """Both models, their batches and work threads, warmed; then `service.warm = True`."""
    import torch

    from app.stt_engine.engine import QwenSTTEngine
    from app.tts_engine.engine import QwenTTSEngine
    from app.work.gpu import CudaGPU
    from app.work.normalize import TextNormalizer
    from app.work.reply_text import QwenReplyText
    from app.work.stt_worker import STTWorker
    from app.work.tts_worker import TTSWorker

    t0 = time.monotonic()
    tts = QwenTTSEngine(str(settings.TTS_MODEL), predictor="ours-fused", talker="ours-graph",
                        sampling={"temperature": settings.TTS_TEMPERATURE, "top_k": settings.TTS_TOP_K,
                                  "top_p": settings.TTS_TOP_P, "repetition_penalty": settings.TTS_REPETITION_PENALTY},
                        instruct=settings.TTS_INSTRUCT)
    tts.load()
    stt = QwenSTTEngine(str(settings.STT_MODEL), max_seconds=settings.STT_MAX_SECONDS)
    stt.load()
    logger.info("models loaded in %.1f s (TTS %s; talker sampling over the model's: %s; instruction: %r)",
                time.monotonic() - t0, settings.TTS_MODEL.name, tts.sampling or "none", tts.instruct)

    def decode(frames) -> np.ndarray:
        return tts._codec.decode([{"audio_codes": torch.stack(list(frames))}])[0][0]

    normalize = TextNormalizer(enabled=settings.TTS_NORMALIZE)
    normalize.warm()                                    # its grammars loaded now, not in the first reply
    tts_worker = TTSWorker(tts.batch(slots=settings.TTS_SLOTS), text=QwenReplyText(tts, normalize),
                           decode=decode, gpu=CudaGPU(tts._generation, tts._decoding))
    stt_worker = STTWorker(stt.batch(slots=settings.STT_SLOTS), tick=settings.STT_TICK_SECONDS)
    tts_worker.start()
    stt_worker.start()
    warm_up(tts_worker, stt_worker)
    logger.info("warm in %.1f s", time.monotonic() - t0)

    def gpu() -> dict:
        free, total = torch.cuda.mem_get_info()
        return {"name": torch.cuda.get_device_name(), "vram_used_gb": round((total - free) / 2**30, 2),
                "vram_total_gb": round(total / 2**30, 2)}

    service.tts, service.stt = tts_worker, stt_worker
    service.voices = {v.name: v.language for v in tts.voices()}
    service.languages = stt.languages()
    service.models = {"tts": str(settings.TTS_MODEL.name), "stt": str(settings.STT_MODEL.name)}
    service.gpu = gpu
    service.warm = True


def warm_up(tts_worker, stt_worker) -> None:
    """A sentence through the TTS's work thread and an utterance through the STT's: the first run of
    each captures its batch's CUDA graphs (the predictor's and the talker's; the STT's kernels start)."""
    spoken, heard = threading.Event(), threading.Event()
    errors: list = []

    def tts_sink(kind, payload):
        if kind == "error":
            errors.append(payload)
        if kind in ("done", "error"):
            spoken.set()

    def stt_sink(kind, payload):
        if kind == "error":
            errors.append(payload)
        if kind in ("final", "error"):
            heard.set()

    tts_worker.say("Hi, how can I help you today?", "ryan", None, tts_sink)
    u = stt_worker.open(None, stt_sink)
    stt_worker.audio(u, np.random.default_rng(0).normal(0, 1e-3, 32000).astype(np.float32))
    stt_worker.end(u)
    if not (spoken.wait(300) and heard.wait(300)) or errors:
        raise RuntimeError(f"warm-up failed: {errors or 'timed out'}")


async def serve(settings: Settings) -> None:
    from service_auth.fastapi_auth import ServiceAuth

    service = Service(tts=None, stt=None, voices={}, languages=[], settings=settings)
    app = create_api(service, ServiceAuth.from_env(AUDIENCE))
    cert, key = settings.TLS_DIR / "cert.pem", settings.TLS_DIR / "key.pem"
    tls = {"ssl_certfile": str(cert), "ssl_keyfile": str(key)} if cert.exists() else {}
    if not tls:
        logger.warning("no certificate in %s: serving plain HTTP (development only)", settings.TLS_DIR)
    server = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=settings.API_PORT, log_config=None,
                                           server_header=False, timeout_graceful_shutdown=5, **tls))
    loading = asyncio.create_task(asyncio.to_thread(load, service, settings))
    serving = asyncio.create_task(server.serve())
    done, _ = await asyncio.wait({loading, serving}, return_when=asyncio.FIRST_COMPLETED)
    if loading in done and loading.exception() is not None:
        logger.error("loading failed: %s", loading.exception())
        server.should_exit = True
        await serving
        raise SystemExit(1)
    await serving
    for worker in (service.tts, service.stt):
        if worker is not None:
            worker.stop()


class _DenialNoise(logging.Filter):
    """uvicorn 0.54's WebSocket protocols (websockets-sansio and wsproto) don't mark the handshake
    complete after a denial response (the 401/403/400/503 we send before accepting: the client gets
    it), then log "ASGI callable returned without completing handshake." as an ERROR. Every session
    here either accepts or refuses (tests/test_api.py), so that line is only noise."""

    def filter(self, record: logging.LogRecord) -> bool:
        return record.getMessage() != "ASGI callable returned without completing handshake."


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("uvicorn.error").addFilter(_DenialNoise())
    asyncio.run(serve(Settings()))


if __name__ == "__main__":
    main()
