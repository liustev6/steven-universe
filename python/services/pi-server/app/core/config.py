"""
Configuration for pi-server.
Loaded from environment variables (prefix PI_SERVER_) and an optional .env file.
Every setting has a default, so the service runs with no configuration.
"""

from pathlib import Path
from typing import Optional

from pydantic import field_validator
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

    # Where device settings are saved (settings.json).
    DATA_DIR: Path = Path("~/.local/share/pi-server")

    # Mock data until home-hub exists.
    MOCK: bool = True
    MOCK_WEATHER_INTERVAL: float = 5.0
    MOCK_REPLY_WORD_DELAY: float = 0.15

    # /debug/* endpoints for pushing test messages with curl.
    DEBUG_ENDPOINTS: bool = True

    # The voice turn (docs/specs/voice-path.md): the mic, speech-service, agent-service, the speaker.
    # Off: the mock assistant answers (MOCK). On the Pi: deploy/pi-server.env turns it on.
    VOICE: bool = False
    AUTH_ISSUER: str = "https://auth.lan.liustev6.ca"
    CLIENT_ID: str = "pi-server"
    CLIENT_SECRET_FILE: Path = Path("/etc/pi-server/credentials/client_secret")
    SPEECH_URL: str = "https://speech.lan.liustev6.ca"
    AGENT_URL: str = "https://agent.lan.liustev6.ca"
    AGENT_PROFILE: str = "pi"                    # its own profile (infra/services/agent-service/profiles.yaml)
    WAKE_WORD: str = "hey_jarvis"                # a built-in microWakeWord model, or a trained one's .json
    LISTEN_WINDOW_SECONDS: float = 5.0           # a session waits this long for someone to speak (V9)
    VOICE_NAME: str = "ryan"
    TTS_LANGUAGE: Optional[str] = None           # None: the voice's own
    STT_LANGUAGE: Optional[str] = None           # None: detected
    SILENCE_MS: int = 700                        # the turn ends after this much silence
    MIC_DEVICE: str = "plughw:CARD=Speakerphone,DEV=0"
    SPEAKER_DEVICE: str = "plughw:CARD=Speakerphone,DEV=0"

    @field_validator("DATA_DIR")
    @classmethod
    def _expand_home(cls, value: Path) -> Path:
        return value.expanduser()


settings = Settings()
