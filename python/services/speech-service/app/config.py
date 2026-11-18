"""Settings (prefix SPEECH_). The environment's values will live in infra/services/speech-service/
config.env (4.6). The limits are T13's."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SPEECH_", extra="ignore")

    API_PORT: int = 8443                       # host 443 → container 8443
    TLS_DIR: Path = Path("/etc/speech-service/tls")   # cert.pem, key.pem for speech.lan.liustev6.ca

    # ---- The models, pinned on disk (T4) ---------------------------------------------------------
    TTS_MODEL: Path = Path("/models/Qwen3-TTS-12Hz-0.6B-CustomVoice")
    STT_MODEL: Path = Path("/models/Qwen3-ASR-0.6B")

    # ---- Concurrency (T13): per kind, SLOTS running (the batch) + WAITING in line -----------------
    TTS_SLOTS: int = 2
    STT_SLOTS: int = 2
    WAITING: int = 1
    RETRY_AFTER_SECONDS: int = 2               # the 503's Retry-After when full

    # ---- Releases (T13) ----------------------------------------------------------------------------
    QUEUE_WAIT_SECONDS: float = 10.0           # waiting for a place, at most
    TTS_IDLE_SECONDS: float = 15.0             # nothing left to speak and no new text
    STT_IDLE_SECONDS: float = 10.0             # no audio
    TTS_MAX_SECONDS: float = 600.0             # one reply
    STT_MAX_SECONDS: float = 60.0              # one utterance
    MAX_TEXT_CHARS: int = 4096                 # one reply's text (T8, OpenAI's limit)

    STT_TICK_SECONDS: float = 0.1              # the STT's batch ticks this often (and at once on an end)
