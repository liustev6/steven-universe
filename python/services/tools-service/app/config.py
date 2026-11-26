"""tools-service settings (environment: TOOLS_*; the token issuer: SERVICE_AUTH_ISSUER, read by service-auth).

Every deploy installs infra/services/tools-service/config.env as /etc/tools-service/config.env.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TOOLS_", extra="ignore")

    # Two listeners (tools.md TL13), both TLS with the same certificate.
    API_PORT: int = 8443                     # host 443: the REST API, for apps
    MCP_PORT: int = 9443                     # host 8443: MCP, for agents
    TLS_DIR: Path = Path("/etc/tools-service/tls")

    # MCP's resource identity (RFC 9728 metadata) and the Host headers it answers (DNS rebinding).
    MCP_URL: str = "https://tools.lan.liustev6.ca:8443/mcp"
    MCP_ALLOWED_HOSTS: list[str] = ["tools.lan.liustev6.ca:8443"]

    # Weather: Open-Meteo (no key). Cached: places a day, weather 5 minutes (its fair-use terms).
    GEOCODING_URL: str = "https://geocoding-api.open-meteo.com/v1/search"
    FORECAST_URL: str = "https://api.open-meteo.com/v1/forecast"
    PLACE_CACHE_SECONDS: int = 86_400
    WEATHER_CACHE_SECONDS: int = 300
    UPSTREAM_TIMEOUT_SECONDS: float = 10.0

    LOG_LEVEL: str = "INFO"
    REVISION: str = "unknown"                # the image's commit (Dockerfile)
