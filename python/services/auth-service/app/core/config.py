"""
Configuration for auth-service.
Loaded from environment variables (prefix AUTH_SERVICE_) and an optional .env file.

Everything that matters for security (policy, client hashes, signing keys) lives
in files under CONFIG_DIR, not in settings. See docs/specs/auth-service.md,
"Client registration and credential lifecycle".
"""

from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AUTH_SERVICE_", env_file=".env", extra="ignore"
    )

    APP_NAME: str = "auth-service"
    APP_VERSION: str = "0.1.0"
    LOG_LEVEL: str = "INFO"

    # /etc/auth-service on the host: policy.yaml, keys/, clients/.
    CONFIG_DIR: Path = Path("/etc/auth-service")
    # Override where policy.yaml is read from (default: CONFIG_DIR/policy.yaml).
    POLICY_PATH: Optional[Path] = None

    # /oauth2/token: requests per minute per (client, IP).
    RATE_LIMIT_PER_MINUTE: int = 30
    # Largest accepted token request body.
    MAX_FORM_BYTES: int = 16 * 1024
    # Clock leeway when checking subject tokens.
    LEEWAY_SECONDS: int = 30

    @property
    def policy_path(self) -> Path:
        return self.POLICY_PATH or self.CONFIG_DIR / "policy.yaml"

    @property
    def keys_dir(self) -> Path:
        return self.CONFIG_DIR / "keys"

    @property
    def clients_dir(self) -> Path:
        return self.CONFIG_DIR / "clients"
