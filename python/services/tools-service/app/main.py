"""tools-service's process: one tool registry, two listeners (tools.md TL13).

    container 8443 (host 443)    the REST API, for apps          (app.api)
    container 9443 (host 8443)   MCP, for agents                 (app.mcp_server)

Both are served by uvicorn in one event loop, as agent-service's two listeners (control-plane.md C1),
and share the tools, the weather cache and the token checks. On SIGTERM both stop together.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal

import httpx
import uvicorn
from service_auth.fastapi_auth import ServiceAuth

from app import pi_tools
from app.api import create_api
from app.config import Settings
from app.mcp_server import AUDIENCE, create_mcp
from app.registry import Registry
from app.weather import tools as weather_tools
from app.weather.openmeteo import OpenMeteo

logger = logging.getLogger("app.main")


def build_registry(settings: Settings, http: httpx.AsyncClient) -> Registry:
    registry = Registry()
    weather_tools.register(registry, OpenMeteo(
        http, geocoding_url=settings.GEOCODING_URL, forecast_url=settings.FORECAST_URL,
        place_ttl=settings.PLACE_CACHE_SECONDS, weather_ttl=settings.WEATHER_CACHE_SECONDS))
    pi_tools.register(registry)
    return registry


class Listener(uvicorn.Server):
    """A uvicorn server that leaves signals to serve(): two servers can't both own SIGTERM."""

    @contextlib.contextmanager
    def capture_signals(self):
        yield


def listener(app, port: int, settings: Settings) -> Listener:
    return Listener(uvicorn.Config(
        app, host="0.0.0.0", port=port, lifespan="off", log_config=None, access_log=True,
        server_header=False, timeout_graceful_shutdown=5,
        ssl_certfile=str(settings.TLS_DIR / "cert.pem"), ssl_keyfile=str(settings.TLS_DIR / "key.pem")))


async def serve(settings: Settings) -> None:
    http = httpx.AsyncClient(timeout=settings.UPSTREAM_TIMEOUT_SECONDS,
                             headers={"User-Agent": "steven-universe-tools-service"})
    registry = build_registry(settings, http)
    auth = ServiceAuth.from_env(AUDIENCE)                       # SERVICE_AUTH_ISSUER
    mcp_server, mcp_app = create_mcp(
        registry, auth.verifier, issuer=os.environ["SERVICE_AUTH_ISSUER"], resource_url=settings.MCP_URL,
        allowed_hosts=settings.MCP_ALLOWED_HOSTS, revision=settings.REVISION)
    servers = [listener(create_api(registry, auth, revision=settings.REVISION), settings.API_PORT, settings),
               listener(mcp_app, settings.MCP_PORT, settings)]
    logger.info("tools: %s", ", ".join(t.location for t in registry))

    def stop() -> None:
        logger.info("stopping: both listeners")
        for s in servers:
            s.should_exit = True

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop)
    try:
        async with mcp_server.session_manager.run():          # lifespan is off: MCP's task group here
            await asyncio.gather(*(s.serve() for s in servers))
    finally:
        await http.aclose()
        logger.info("stopped")


def main() -> None:
    settings = Settings()
    logging.basicConfig(level=settings.LOG_LEVEL, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    asyncio.run(serve(settings))


if __name__ == "__main__":
    main()
