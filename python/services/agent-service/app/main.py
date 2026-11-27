"""agent-service's process: one sessions core, two listeners (control-plane.md C1).

    container 8443 (host 443)    the sessions API, for callers            (app/api)
    container 9443 (host 8443)   the gateway, for sandboxes only          (app/gateway)

Both are served by uvicorn in one event loop, so they share the sessions core's memory (each live
session's outbox, translator, capability map). Start-up, in order:

    1. profiles.yaml (a bad file: the service doesn't start)
    2. migrations (Alembic, to the newest; applied ones are skipped)
    3. the login: the newer of the disk's and OpenBao's (the broker)
    4. live sessions reloaded from PostgreSQL (C5): their runtimes' long polls reconnect
    5. both listeners, the sweeper (timers, lost sandboxes) and the login's refresh loop

On SIGTERM both listeners stop together; open streams get 5 s. Sessions are NOT ended: they
survive the restart (C5), and the runtimes reconnect to the next process.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal

import httpx
import uvicorn

from app.api import AUDIENCE, create_api
from app.broker import AuthBroker, OpenBaoStore
from app.config import Settings
from app.db import make_engine, make_sessionmaker, upgrade
from app.file_management import FileManagement
from app.gateway import create_gateway
from app.tools_service import ToolsService
from app.history.eventlog import EventLog
from app.profiles import load_profiles
from app.sessions import Sessions
from app.sessions.runner import HttpRunner

logger = logging.getLogger("app.main")


class Listener(uvicorn.Server):
    """A uvicorn server that leaves signals to main(): two servers can't both own SIGTERM."""

    @contextlib.contextmanager
    def capture_signals(self):
        yield


def listener(app, port: int, settings: Settings, *, access_log: bool) -> Listener:
    return Listener(uvicorn.Config(
        app, host="0.0.0.0", port=port, lifespan="off", log_config=None, access_log=access_log,
        server_header=False, timeout_graceful_shutdown=5,
        ssl_certfile=str(settings.TLS_DIR / "cert.pem"), ssl_keyfile=str(settings.TLS_DIR / "key.pem")))


async def serve(settings: Settings) -> None:
    from service_auth import TokenClient
    from service_auth.fastapi_auth import ServiceAuth

    profiles = load_profiles(settings.PROFILES_FILE)
    url = settings.database_url()
    await asyncio.to_thread(upgrade, url)
    logger.info("database migrated; %d profile(s): %s", len(profiles), ", ".join(sorted(profiles)))

    tokens = TokenClient.from_env()            # SERVICE_AUTH_ISSUER, _CLIENT_ID, _CLIENT_SECRET_FILE
    engine = make_engine(url)
    db = make_sessionmaker(engine)
    runner = HttpRunner.connect(settings.RUNNER_URL, tokens)
    store = OpenBaoStore(settings.BAO_ADDR, tokens)
    broker = AuthBroker(settings.CODEX_HOME, store, refresh_every=settings.LOGIN_REFRESH_HOURS * 3600)
    upstream = httpx.AsyncClient(base_url=settings.MODEL_UPSTREAM, timeout=httpx.Timeout(30.0, read=600.0))
    tools = ToolsService(tokens, settings.TOOLS_MCP_URL) if settings.TOOLS_MCP_URL else None   # tools.md
    files = FileManagement(tokens, settings.FILES_URL) if settings.FILES_URL else None   # files-and-mounts.md
    sessions = Sessions(db, EventLog(db), runner, profiles, settings, tools, files)

    await broker.restore()
    await sessions.load()

    gateway = create_gateway(sessions, broker, upstream)
    servers = [
        listener(create_api(sessions, ServiceAuth.from_env(AUDIENCE), broker), settings.API_PORT, settings,
                 access_log=True),
        # The gateway's requests (long polls, event batches) are many and routine: no access log.
        listener(gateway, settings.GATEWAY_PORT, settings, access_log=False),
    ]

    def stop() -> None:
        logger.info("stopping: both listeners (sessions stay; runtimes reconnect to the next process)")
        for s in servers:
            s.should_exit = True

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop)
    background = [asyncio.create_task(sessions.run_sweeper(), name="sweeper"),
                  asyncio.create_task(broker.keep_fresh(), name="login refresh")]
    try:
        async with gateway.state.mcp.session_manager.run():     # /mcp's task group (lifespan is off)
            await asyncio.gather(*(s.serve() for s in servers))
    finally:
        for task in background:
            task.cancel()
        await asyncio.gather(*background, return_exceptions=True)
        await runner.aclose()
        await store.aclose()
        await upstream.aclose()
        if files is not None:
            await files.aclose()
        await engine.dispose()
        logger.info("stopped")


def main() -> None:
    settings = Settings()
    logging.basicConfig(level=settings.LOG_LEVEL, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)       # one line per request otherwise
    logging.getLogger("alembic.runtime.plugins").setLevel(logging.WARNING)   # its set-up chatter
    asyncio.run(serve(settings))


if __name__ == "__main__":
    main()
