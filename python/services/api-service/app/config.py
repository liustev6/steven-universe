"""Settings (prefix API_). Environment values live in infra/services/api-service/config.env (D12)."""

from __future__ import annotations

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

    LOG_LEVEL: str = "INFO"
