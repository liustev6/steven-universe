"""The auth broker (model-access.md), milestone-1 version: your ChatGPT login, held on the trusted side.

    CODEX_HOME/auth.json     written once by `python -m agent_dev.login` (device code, approved on your phone)
    credentials()            the current access token and account id, read from auth.json
    refresh()                Codex refreshes it (the SDK's account refresh), never our own token call (MA2)

It is the only process that refreshes the login: the sandbox never sees auth.json (MA1). In
agent-service the broker also saves each refreshed bundle to OpenBao (MA3); here the file lives in
dev/secrets/ (gitignored).
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

logger = logging.getLogger("agent_dev.broker")

# Credentials in a file (never the OS keyring) so the broker can read and persist them.
CODEX_OVERRIDES = ('cli_auth_credentials_store="file"', "check_for_update_on_startup=false",
                   "analytics.enabled=false", "feedback.enabled=false")


def codex_config(codex_home: Path):
    from openai_codex import CodexConfig

    return CodexConfig(config_overrides=CODEX_OVERRIDES, env={"CODEX_HOME": str(codex_home)})


async def codex_refresh(codex_home: Path) -> None:
    """Ask Codex for a proactive refresh: it runs the refresh-token flow and rewrites auth.json."""
    from openai_codex import AsyncCodex

    async with AsyncCodex(codex_config(codex_home)) as codex:
        await codex.account(refresh_token=True)


class NotSignedIn(RuntimeError):
    pass


class AuthBroker:
    def __init__(self, codex_home: Path, *, refresh_every: float = 6 * 3600,
                 refresher: Callable[[Path], Awaitable[None]] = codex_refresh):
        self.codex_home = codex_home
        self.refresh_every = refresh_every
        self._refresher = refresher
        self._lock = asyncio.Lock()

    @property
    def auth_file(self) -> Path:
        return self.codex_home / "auth.json"

    def credentials(self) -> tuple[str, str | None]:
        """(access token, ChatGPT account id). Read per request: a refresh may have just rewritten it."""
        try:
            auth = json.loads(self.auth_file.read_text())
        except FileNotFoundError:
            raise NotSignedIn(f"no login at {self.auth_file}: run `python -m agent_dev.login`") from None
        tokens = auth.get("tokens") or {}
        if auth.get("auth_mode") not in (None, "chatgpt") or not tokens.get("access_token"):
            raise NotSignedIn(f"{self.auth_file} holds no ChatGPT login")
        return tokens["access_token"], tokens.get("account_id")

    async def refresh(self) -> None:
        """One refresh at a time: two refreshers would race on a rotating refresh token."""
        async with self._lock:
            before = self._last_refresh()
            await self._refresher(self.codex_home)
            logger.info("login refreshed (last_refresh %s -> %s)", before, self._last_refresh())

    async def keep_fresh(self) -> None:
        """Refresh regularly, so the refresh token stays in use and never lapses (MA4)."""
        while True:
            await asyncio.sleep(self.refresh_every)
            try:
                await self.refresh()
            except Exception:  # the next request's 401 retries it; the model route reports a failure
                logger.exception("scheduled refresh failed")

    def _last_refresh(self) -> str | None:
        try:
            return json.loads(self.auth_file.read_text()).get("last_refresh")
        except (OSError, ValueError):
            return None
