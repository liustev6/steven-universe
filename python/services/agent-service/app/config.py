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

    # ---- The two listeners (C1): host 443 -> API_PORT, host 8443 -> GATEWAY_PORT ----------------
    API_PORT: int = 8443                         # the sessions API, for callers
    GATEWAY_PORT: int = 9443                     # the gateway, for sandboxes only (VM 200's firewall)
    TLS_DIR: Path = Path("/etc/agent-service/tls")   # cert.pem, key.pem for agent.lan.liustev6.ca (both ports)

    # ---- What agents can be (C12) --------------------------------------------------------------
    PROFILES_FILE: Path = Path("/etc/agent-service/profiles.yaml")

    # ---- History: its own PostgreSQL, a container in the same compose project (C8) ------------
    DB_HOST: str = "postgres"
    DB_PORT: int = 5432
    DB_NAME: str = "agent"
    DB_USER: str = "agent"
    DB_PASSWORD_FILE: Path = Path("/run/secrets/postgres_password")

    # ---- Sandboxes: claimed from sandbox-runner on VM 200 (C6) ---------------------------------
    RUNNER_URL: str = "https://sandbox-runner.lan.liustev6.ca"
    # The runtime's turns, events and model calls reach agent-service through the pod's proxy,
    # which forwards to the runner's configured gateway (its RUNNER_GATEWAY_URL): this service's
    # port 8443.

    # ---- Session timers, checked by a sweep every SWEEP_SECONDS (C6) --------------------------
    IDLE_SECONDS: int = 300                      # no turn for this long: the session ends, its pod released
    READY_TIMEOUT_SECONDS: int = 60              # claimed, but the runtime never said ready
    SWEEP_SECONDS: int = 15
    POLL_WAIT_MAX: int = 30                      # the runtime's long poll, at most

    # ---- History carried into a new session (C9) ----------------------------------------------
    CARRY_CHARS: int = 4000                      # each carried message, at most

    # ---- The model, and your login (the auth broker, C11) -------------------------------------
    MODEL_UPSTREAM: str = "https://chatgpt.com/backend-api/codex"   # your plan's endpoint
    CODEX_HOME: Path = Path("/var/lib/agent-service/codex-home")   # auth.json, refreshed by Codex
    LOGIN_REFRESH_HOURS: float = 6                                 # keeps the refresh token in use
    BAO_ADDR: str = "https://bao.lan.liustev6.ca:8200"             # the login's durable copy (MA3)

    # Callers and the runner: auth-service tokens (SERVICE_AUTH_ISSUER, SERVICE_AUTH_CLIENT_ID,
    # SERVICE_AUTH_CLIENT_SECRET_FILE; service-auth reads them).

    LOG_LEVEL: str = "INFO"

    def database_url(self) -> URL:
        """SQLAlchemy's URL object, so the password is never pasted into a string (or a log line)."""
        return URL.create("postgresql+psycopg", username=self.DB_USER,
                          password=self.DB_PASSWORD_FILE.read_text().strip(),
                          host=self.DB_HOST, port=self.DB_PORT, database=self.DB_NAME)
