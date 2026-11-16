"""Settings (prefix AGENT_). The environment's values live in infra/services/agent-service/config.env,
the profiles in profiles.yaml next to it (C12). Secrets are read from files, never from environment
variables.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AGENT_", extra="ignore")

    # ---- What agents can be (C12) --------------------------------------------------------------
    PROFILES_FILE: Path = Path("/etc/agent-service/profiles.yaml")

    # ---- History: its own PostgreSQL, a container in the same compose project (C8) ------------
    DB_HOST: str = "postgres"
    DB_PORT: int = 5432
    DB_NAME: str = "agent"
    DB_USER: str = "agent"
    DB_PASSWORD_FILE: Path = Path("/run/secrets/postgres_password")

    LOG_LEVEL: str = "INFO"

    def database_url(self) -> URL:
        """SQLAlchemy's URL object, so the password is never pasted into a string (or a log line)."""
        return URL.create("postgresql+psycopg", username=self.DB_USER,
                          password=self.DB_PASSWORD_FILE.read_text().strip(),
                          host=self.DB_HOST, port=self.DB_PORT, database=self.DB_NAME)
