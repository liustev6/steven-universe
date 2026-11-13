"""Settings (prefix API_). Environment values live in infra/services/api-service/config.env (D12)."""

from __future__ import annotations

from typing import Any

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="API_", extra="ignore")

    # Client IP (spec "Request pipeline" 2): CF-Connecting-IP is trusted only on requests that come
    # from cloudflared itself; anyone else could set that header.
    TRUSTED_PROXIES: list[str] = ["192.168.50.53"]   # cloudflared (CT 100)

    # ---- /deploy/* -> the deployer (spec "Deploys", mode `proxy`) ------------------------------
    DEPLOYER_URL: str = "https://deploy.lan.liustev6.ca"
    # Cheap pre-checks; the deployer does the real verification (signature, every claim).
    DEPLOY_TOKEN_ISSUER: str = "https://token.actions.githubusercontent.com"
    DEPLOY_TOKEN_AUDIENCE: str = "steven-universe-deployer"
    DEPLOY_MAX_BODY_BYTES: int = 4096
    # Per client IP: a burst of DEPLOY_RATE_BURST, refilled at DEPLOY_RATE_PER_MINUTE.
    DEPLOY_RATE_BURST: int = 10
    DEPLOY_RATE_PER_MINUTE: float = 10
    # Reconnects after a dropped stream (the workflow retries every few seconds while api-service
    # itself restarts): more generous.
    DEPLOY_EVENTS_RATE_BURST: int = 30
    DEPLOY_EVENTS_RATE_PER_MINUTE: float = 60
    # The deployer streams progress (with heartbeats every 15 s); a deploy may take minutes.
    DEPLOY_READ_TIMEOUT_SECONDS: float = 120

    # ---- /v1/status: the lab's public status page (spec "Status") ---------------------------------
    # [{"name", "url", "kind": health|openbao|status, "audience"}]: kind status uses api-service's own
    # token (scope status:read; SERVICE_AUTH_ISSUER, _CLIENT_ID, _CLIENT_SECRET_FILE).
    STATUS_TARGETS: list[Any] = []
    STATUS_CACHE_SECONDS: float = 5
    STATUS_TIMEOUT_SECONDS: float = 2
    STATUS_RATE_BURST: int = 30
    STATUS_RATE_PER_MINUTE: float = 60
    REVISION: str = "unknown"            # the image's commit (set in the Dockerfile from its build arg)

    LOG_LEVEL: str = "INFO"

    def model_post_init(self, __context: Any) -> None:
        from app.status import StatusTarget
        self.STATUS_TARGETS = [StatusTarget.model_validate(t) for t in self.STATUS_TARGETS]
