"""The auth broker (model-access.md MA1–MA6, control-plane.md C11): your ChatGPT login, held only here.

    restore()     at start: the newer of the disk's copy and OpenBao's, so a rebuilt host comes back
                  signed in (MA3) and a stale copy never replaces a newer one (refresh tokens rotate)
    headers()     per model request: the current access token and account id, read from auth.json
                  (a refresh may have just rewritten it)
    refresh()     Codex refreshes it; one at a time, and callers who waited behind a refresh reuse it
    keep_fresh()  every few hours, so the refresh token stays in use and never lapses (MA4)
    status()      for the status page: signed in, the login's age, whether OpenBao has it

Every refresh that changes the login is saved to OpenBao. A failed save is logged and shown on the
status page, never fatal: the model keeps working from the disk's copy.

This process is the only one that refreshes the login (MA2): the sandboxes never see it (MA1).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.broker.base import NotSignedIn
from app.broker.codex import codex_refresh
from app.broker.store import LoginStore, StoreError

logger = logging.getLogger(__name__)


class AuthBroker:
    def __init__(self, codex_home: Path, store: LoginStore | None, *, refresh_every: float = 6 * 3600,
                 refresher: Callable[[Path], Awaitable[None]] = codex_refresh):
        self.codex_home = codex_home
        self.store = store
        self.refresh_every = refresh_every
        self._refresher = refresher
        self._lock = asyncio.Lock()
        self._refreshed_at = 0.0                     # monotonic time of the last refresh
        self._saved: str | None = None               # what OpenBao last received
        self._saved_at: datetime | None = None
        self._save_error: str | None = None

    @property
    def auth_file(self) -> Path:
        return self.codex_home / "auth.json"

    # ---- per request -----------------------------------------------------------------------------
    def headers(self) -> dict[str, str]:
        token, account_id = self._credentials()
        headers = {"authorization": f"Bearer {token}"}
        if account_id:
            headers["chatgpt-account-id"] = account_id
        return headers

    def _credentials(self) -> tuple[str, str | None]:
        try:
            auth = json.loads(self.auth_file.read_text())
        except FileNotFoundError:
            raise NotSignedIn(f"no login at {self.auth_file}: run `python -m app.broker login` once") from None
        except ValueError:
            raise NotSignedIn(f"{self.auth_file} isn't valid JSON") from None
        tokens = auth.get("tokens") or {}
        if auth.get("auth_mode") not in (None, "chatgpt") or not tokens.get("access_token"):
            raise NotSignedIn(f"{self.auth_file} holds no ChatGPT login")
        return tokens["access_token"], tokens.get("account_id")

    # ---- refresh ---------------------------------------------------------------------------------
    async def refresh(self) -> None:
        """Codex refreshes the login. Several sessions may hit an expired token at once: the first
        refreshes, the others find a refresh newer than their request and reuse it."""
        asked = time.monotonic()
        async with self._lock:
            if self._refreshed_at > asked:
                return
            before = self._last_refresh()
            await self._refresher(self.codex_home)
            self._refreshed_at = time.monotonic()
            logger.info("login refreshed (last_refresh %s -> %s)", before, self._last_refresh())
        await self.save()

    async def keep_fresh(self) -> None:
        while True:
            await asyncio.sleep(self.refresh_every)
            try:
                await self.refresh()
            except Exception:                        # the next request's 401 retries; status shows its age
                logger.exception("scheduled refresh failed")

    # ---- the durable copy in OpenBao (MA3) -------------------------------------------------------
    async def save(self) -> None:
        """Save the disk's login to OpenBao if it changed since the last save."""
        bundle = self._read_disk()
        if self.store is None or bundle is None or bundle == self._saved:
            return
        try:
            await self.store.save(bundle)
        except StoreError as exc:
            self._save_error = str(exc)
            logger.error("couldn't save the login to OpenBao: %s (the disk's copy still works)", exc)
            return
        self._saved, self._saved_at, self._save_error = bundle, datetime.now(UTC), None
        logger.info("login saved to OpenBao")

    async def restore(self) -> None:
        """At start: keep whichever copy was refreshed last, on disk or in OpenBao (the disk's wins
        a tie: it's the one in use). Then make sure OpenBao has it."""
        disk = self._read_disk()
        saved = None
        if self.store is not None:
            try:
                saved = await self.store.load()
            except StoreError as exc:
                self._save_error = str(exc)
                logger.error("couldn't read the login from OpenBao: %s", exc)
        copies = [b for b in (disk, saved) if b is not None]
        if not copies:
            logger.warning("not signed in: run `python -m app.broker login` once on this host")
            return
        newest = max(copies, key=_refreshed)
        if newest != disk:
            self._write_disk(newest)
            logger.info("login restored from OpenBao (last_refresh %s)", _refreshed(newest).isoformat())
        if newest == saved:
            self._saved, self._saved_at = saved, datetime.now(UTC)
        else:
            await self.save()                        # OpenBao has none, or an older one

    # ---- the status page -------------------------------------------------------------------------
    def status(self) -> dict[str, Any]:
        try:
            self._credentials()
            signed_in = True
        except NotSignedIn:
            signed_in = False
        last = _refreshed(self._read_disk() or "")
        age = None if last == datetime.min.replace(tzinfo=UTC) else round(
            (datetime.now(UTC) - last).total_seconds() / 3600, 1)
        return {"signed_in": signed_in, "refreshed_hours_ago": age,
                "saved_to_openbao": self._save_error is None and self._saved is not None}

    # ---- the file --------------------------------------------------------------------------------
    def _read_disk(self) -> str | None:
        try:
            return self.auth_file.read_text()
        except FileNotFoundError:
            return None

    def _write_disk(self, bundle: str) -> None:
        """Atomically, readable by this process only."""
        self.codex_home.mkdir(mode=0o700, parents=True, exist_ok=True)
        tmp = self.auth_file.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(bundle)
        tmp.replace(self.auth_file)

    def _last_refresh(self) -> str | None:
        try:
            return json.loads(self._read_disk() or "{}").get("last_refresh")
        except ValueError:
            return None


def _refreshed(bundle: str) -> datetime:
    """A bundle's `last_refresh` (Codex writes it); unknown counts as oldest."""
    try:
        value = json.loads(bundle).get("last_refresh")
        when = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return when if when.tzinfo else when.replace(tzinfo=UTC)
    except (ValueError, AttributeError, TypeError):
        return datetime.min.replace(tzinfo=UTC)
