"""
Configuration for pi-server.
Loaded from environment variables (prefix PI_SERVER_) and an optional .env file.
Every setting has a default, so the service runs with no configuration.
"""

from pathlib import Path

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

    @field_validator("DATA_DIR")
    @classmethod
    def _expand_home(cls, value: Path) -> Path:
        return value.expanduser()


settings = Settings()
