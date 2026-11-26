"""
Configuration for pi-server.
Loaded from environment variables (prefix PI_SERVER_) and an optional .env file.
Every setting has a default, so the service runs with no configuration.
"""

from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings."""

    model_config = SettingsConfigDict(
        env_prefix="PI_SERVER_", env_file=".env", extra="ignore"
    )

    APP_NAME: str = "pi-server"
    APP_VERSION: str = "0.1.0"
    LOG_LEVEL: str = "INFO"

    # Localhost only: hey-pi runs on the same Pi.
    HOST: str = "127.0.0.1"
    PORT: int = 8765

    # /debug/* endpoints (a speech state for the screen, a typed or spoken turn) with curl.
    DEBUG_ENDPOINTS: bool = True

    # The voice turn (docs/specs/voice-path.md): the mic, speech-service, agent-service, the speaker.
    # Off (the default, e.g. on a Mac): no mic, the screen is told `idle`. On the Pi:
    # deploy/pi-server.env turns it on.
    VOICE: bool = False
    AUTH_ISSUER: str = "https://auth.lan.liustev6.ca"
    CLIENT_ID: str = "pi-server"
    CLIENT_SECRET_FILE: Path = Path("/etc/pi-server/credentials/client_secret")
    SPEECH_URL: str = "https://speech.lan.liustev6.ca"
    AGENT_URL: str = "https://agent.lan.liustev6.ca"
    AGENT_PROFILE: str = "pi"                    # its own profile (infra/services/agent-service/profiles.yaml)
    # The panel's Additional Client Info, added to the profile's instructions (agent-service C15)
    # with the time at each session start; empty: left out (the time is then UTC)
    LOCATION: str = ""                           # city, country: e.g. "Seattle, United States"
    TIMEZONE: str = ""                           # an IANA name, e.g. "America/Los_Angeles"
    WAKE_WORD: str = "models/xiao_pai_04f.json"  # 小派小派, spike 04f at 0.96 (docs/specs/wake-word.md); also 04b, hey_pi
    LISTEN_WINDOW_SECONDS: float = 7.0           # a session waits this long for someone to speak (V9): room for a follow-up
    VOICE_EN: str = "ryan"                       # a voice session that starts in English speaks with this
    VOICE_ZH: str = "dylan"                      # ... in Chinese (mixed: its main language); kept all session
    TTS_LANGUAGE: Optional[str] = None           # None: Auto (the model reads it from the text)
    STT_LANGUAGE: Optional[str] = None           # None: detected
    # Speech-to-text's context (speech-service T18): who speaks to Hey Pi, then the session's last turns
    STT_CONTEXT: str = "The speaker may speak English, Chinese, or both mixed in one sentence."
    SILENCE_MS: int = 700                        # the turn ends after this much silence
    MIC_DEVICE: str = "plughw:CARD=Speakerphone,DEV=0"
    SPEAKER_DEVICE: str = "plughw:CARD=Speakerphone,DEV=0"


settings = Settings()
