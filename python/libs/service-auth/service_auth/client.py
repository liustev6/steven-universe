"""Getting tokens from auth-service (spec: "Token client").

A service holds one TokenClient. It mints tokens with the service's client
credentials, caches each one, and renews it shortly before it expires, so
calling services don't wait on auth-service per request.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncGenerator, Awaitable, Callable, Iterable, Mapping
from urllib.parse import quote_plus

import httpx

from .constants import (
    GRANT_CLIENT_CREDENTIALS,
    GRANT_TOKEN_EXCHANGE,
    METADATA_PATH,
    TOKEN_TYPE_ACCESS_TOKEN,
    TOKEN_TYPE_USER_ID,
    USER_PREFIX,
)
from .errors import TokenRequestError
from .principal import Principal

logger = logging.getLogger(__name__)

Details = Iterable[Mapping[str, Any]]


@dataclass
class _Cached:
    token: str
    expires_at: float  # local monotonic clock


def _scope_param(scopes: Iterable[str] | None) -> str | None:
    return " ".join(sorted(set(scopes))) if scopes else None


def _details_param(details: Details | None) -> str | None:
    if not details:
        return None
    return json.dumps([dict(d) for d in details], sort_keys=True, separators=(",", ":"))


def _basic_auth(client_id: str, secret: str) -> str:
    # RFC 6749 §2.3.1: form-urlencode both parts before base64.
    raw = f"{quote_plus(client_id)}:{quote_plus(secret)}".encode()
    return "Basic " + base64.b64encode(raw).decode()


class TokenClient:
    """Mints, exchanges, and caches access tokens for one client.

    ```python
    tokens = TokenClient.from_env()
    files = httpx.AsyncClient(auth=tokens.auth("file-management", scopes=["files:read"]))
    ```
    """

    def __init__(
        self,
        issuer: str,
        client_id: str,
        client_secret: str,
        *,
        http: httpx.AsyncClient | None = None,
        token_endpoint: str | None = None,
        renew_before: float = 60,
        max_attempts: int = 3,
        backoff: float = 0.2,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self.issuer = issuer.rstrip("/")
        self.client_id = client_id
        self._authorization = _basic_auth(client_id, client_secret)
        self._http = http or httpx.AsyncClient(timeout=10.0)
        self._owns_http = http is None
        self._token_endpoint = token_endpoint
        self._renew_before = renew_before
        self._max_attempts = max_attempts
        self._backoff = backoff
        self._clock = clock
        self._sleep = sleep
        self._cache: dict[tuple, _Cached] = {}
        self._locks: dict[tuple, asyncio.Lock] = {}

    @classmethod
    def from_env(cls, *, http: httpx.AsyncClient | None = None) -> "TokenClient":
        """Build from SERVICE_AUTH_ISSUER, SERVICE_AUTH_CLIENT_ID, and
        SERVICE_AUTH_CLIENT_SECRET_FILE.

        The secret must come from a file (the `_FILE` convention); a secret in an
        environment variable is refused, because environment variables leak
        through /proc, crash dumps, and `docker inspect`.
        """
        if "SERVICE_AUTH_CLIENT_SECRET" in os.environ:
            raise RuntimeError(
                "SERVICE_AUTH_CLIENT_SECRET is not allowed; put the secret in a file "
                "and set SERVICE_AUTH_CLIENT_SECRET_FILE"
            )
        secret_file = os.environ.get("SERVICE_AUTH_CLIENT_SECRET_FILE")
        if not secret_file and os.environ.get("CREDENTIALS_DIRECTORY"):
            # systemd LoadCredential=client_secret:...
            secret_file = str(Path(os.environ["CREDENTIALS_DIRECTORY"]) / "client_secret")
        if not secret_file:
            raise RuntimeError("SERVICE_AUTH_CLIENT_SECRET_FILE is not set")
        secret = Path(secret_file).read_text().strip()
        return cls(
            os.environ["SERVICE_AUTH_ISSUER"],
            os.environ["SERVICE_AUTH_CLIENT_ID"],
            secret,
            http=http,
        )

    # ---- grants ---------------------------------------------------------------

    async def token(
        self, audience: str, scopes: Iterable[str] | None = None, details: Details | None = None
    ) -> str:
        """Grant 1: a token for `audience` as this service itself (cached)."""
        form = {"grant_type": GRANT_CLIENT_CREDENTIALS, "resource": audience}
        return await self._cached(("cc", audience), form, scopes, details)

    async def assert_user(
        self,
        user_id: str,
        audience: str,
        scopes: Iterable[str] | None = None,
        details: Details | None = None,
    ) -> str:
        """Grant 2: start a delegation for a user this service authenticated (cached)."""
        form = {
            "grant_type": GRANT_TOKEN_EXCHANGE,
            "subject_token": f"{USER_PREFIX}{user_id}",
            "subject_token_type": TOKEN_TYPE_USER_ID,
            "resource": audience,
        }
        return await self._cached(("user", audience, user_id), form, scopes, details)

    async def exchange(
        self,
        subject_token: str,
        audience: str,
        scopes: Iterable[str] | None = None,
        details: Details | None = None,
    ) -> str:
        """Grant 3: pass the identity in a token this service received on to `audience` (cached)."""
        form = {
            "grant_type": GRANT_TOKEN_EXCHANGE,
            "subject_token": subject_token,
            "subject_token_type": TOKEN_TYPE_ACCESS_TOKEN,
            "resource": audience,
        }
        subject_key = hashlib.sha256(subject_token.encode()).hexdigest()
        return await self._cached(("exchange", audience, subject_key), form, scopes, details)

    async def downscope(
        self,
        subject_token: str,
        audience: str,
        *,
        scopes: Iterable[str] | None = None,
        details: Details | None = None,
        expires_in: int | None = None,
    ) -> dict[str, Any]:
        """Grant 4: a strictly narrower copy of one of this service's own tokens,
        for a sandbox or a share link. Not cached; returns the token response."""
        form = {
            "grant_type": GRANT_TOKEN_EXCHANGE,
            "subject_token": subject_token,
            "subject_token_type": TOKEN_TYPE_ACCESS_TOKEN,
            "requested_token_type": TOKEN_TYPE_ACCESS_TOKEN,
            "resource": audience,
        }
        if expires_in is not None:
            form["expires_in"] = str(expires_in)
        self._add_narrowing(form, scopes, details)
        return await self._request(form)

    # ---- httpx integration ----------------------------------------------------

    def auth(
        self, audience: str, scopes: Iterable[str] | None = None, details: Details | None = None
    ) -> httpx.Auth:
        """An httpx Auth that sends this service's own token for `audience`."""
        return _TokenAuth(
            lambda: self.token(audience, scopes, details),
            lambda: self._invalidate(("cc", audience), scopes, details),
        )

    def auth_as_user(
        self,
        user_id: str,
        audience: str,
        scopes: Iterable[str] | None = None,
        details: Details | None = None,
    ) -> httpx.Auth:
        """An httpx Auth that acts for a user this service authenticated (grant 2)."""
        return _TokenAuth(
            lambda: self.assert_user(user_id, audience, scopes, details),
            lambda: self._invalidate(("user", audience, user_id), scopes, details),
        )

    def auth_for(
        self,
        principal: Principal,
        audience: str,
        scopes: Iterable[str] | None = None,
        details: Details | None = None,
    ) -> httpx.Auth:
        """An httpx Auth that acts for the caller in `principal` (grant 3)."""
        if principal.token is None:
            raise ValueError("principal has no token to exchange")
        subject_key = hashlib.sha256(principal.token.encode()).hexdigest()
        return _TokenAuth(
            lambda: self.exchange(principal.token, audience, scopes, details),  # type: ignore[arg-type]
            lambda: self._invalidate(("exchange", audience, subject_key), scopes, details),
        )

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    # ---- internals -------------------------------------------------------------

    @staticmethod
    def _add_narrowing(
        form: dict[str, str], scopes: Iterable[str] | None, details: Details | None
    ) -> None:
        scope = _scope_param(scopes)
        if scope:
            form["scope"] = scope
        detail_json = _details_param(details)
        if detail_json:
            form["authorization_details"] = detail_json

    def _invalidate(
        self, base_key: tuple, scopes: Iterable[str] | None, details: Details | None
    ) -> None:
        self._cache.pop(base_key + (_scope_param(scopes), _details_param(details)), None)

    async def _cached(
        self,
        base_key: tuple,
        form: dict[str, str],
        scopes: Iterable[str] | None,
        details: Details | None,
    ) -> str:
        key = base_key + (_scope_param(scopes), _details_param(details))
        self._add_narrowing(form, scopes, details)
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:  # concurrent callers share one request
            cached = self._cache.get(key)
            now = self._clock()
            if cached and now < cached.expires_at - self._renew_before:
                return cached.token
            try:
                body = await self._request(form)
            except TokenRequestError as exc:
                if exc.transient and cached and now < cached.expires_at:
                    logger.warning("auth-service unavailable; using cached token (%s)", exc)
                    return cached.token
                raise
            self._prune()
            self._cache[key] = _Cached(body["access_token"], self._clock() + float(body["expires_in"]))
            return body["access_token"]

    def _prune(self) -> None:
        now = self._clock()
        for key in [k for k, v in self._cache.items() if v.expires_at <= now]:
            self._cache.pop(key, None)
            self._locks.pop(key, None)

    async def _endpoint(self) -> str:
        if self._token_endpoint is None:
            response = await self._http.get(self.issuer + METADATA_PATH)
            response.raise_for_status()
            self._token_endpoint = response.json()["token_endpoint"]
        return self._token_endpoint

    async def _request(self, form: dict[str, str]) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(self._max_attempts):
            if attempt:
                await self._sleep(self._backoff * 2 ** (attempt - 1))
            try:
                response = await self._http.post(
                    await self._endpoint(),
                    data=form,
                    headers={"Authorization": self._authorization, "Accept": "application/json"},
                )
            except httpx.HTTPError as exc:
                last_error = exc
                continue
            if response.status_code >= 500 or response.status_code == 429:
                last_error = TokenRequestError("unavailable", None, response.status_code)
                continue
            try:
                body = response.json()
            except ValueError:
                body = {}
            if response.status_code != 200:
                raise TokenRequestError(
                    body.get("error", "unknown_error"),
                    body.get("error_description"),
                    response.status_code,
                )
            return body
        if isinstance(last_error, TokenRequestError):
            raise last_error
        raise TokenRequestError("unavailable", str(last_error))


class _TokenAuth(httpx.Auth):
    """Adds a Bearer token; on a 401, drops the cached token and retries once."""

    def __init__(
        self, fetch: Callable[[], Awaitable[str]], invalidate: Callable[[], None]
    ):
        self._fetch = fetch
        self._invalidate = invalidate

    def sync_auth_flow(self, request: httpx.Request):  # pragma: no cover - guard only
        raise RuntimeError("TokenClient auth works with httpx.AsyncClient only")

    async def async_auth_flow(
        self, request: httpx.Request
    ) -> AsyncGenerator[httpx.Request, httpx.Response]:
        request.headers["Authorization"] = f"Bearer {await self._fetch()}"
        response = yield request
        if response.status_code == 401:
            self._invalidate()
            request.headers["Authorization"] = f"Bearer {await self._fetch()}"
            yield request
