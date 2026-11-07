"""Checking access tokens locally (spec: "Verifier").

A receiver checks every token itself, with the issuer's public keys (JWKS)
cached in memory. No call to auth-service happens per request.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable

import httpx
import jwt

from .constants import ACCESS_TOKEN_TYP, METADATA_PATH, REQUIRED_CLAIMS, SIGNING_ALG
from .errors import InvalidToken, JWKSUnavailable
from .principal import Principal

logger = logging.getLogger(__name__)


def read_kid(token: str) -> str:
    """Check the JWT header and return its key id.

    `alg` comes from an allowlist and is never trusted from the token, which
    blocks `none` and HMAC-confusion attacks. `typ` must be `at+jwt` (RFC 9068),
    so ID tokens or other JWTs can't be replayed as access tokens.
    """
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise InvalidToken("malformed token") from exc
    if header.get("alg") != SIGNING_ALG:
        raise InvalidToken(f"unexpected alg {header.get('alg')!r}")
    if header.get("typ") != ACCESS_TOKEN_TYP:
        raise InvalidToken(f"unexpected typ {header.get('typ')!r}")
    kid = header.get("kid")
    if not isinstance(kid, str) or not kid:
        raise InvalidToken("missing kid")
    return kid


def decode_claims(
    token: str,
    key: Any,
    *,
    issuer: str,
    audience: str | None,
    leeway: int = 30,
) -> dict[str, Any]:
    """Verify the signature and standard claims; return the claims.

    `audience=None` skips the audience check. Only auth-service does that, when
    it checks a subject token during token exchange, and it then checks `aud`
    itself.
    """
    options = {"require": list(REQUIRED_CLAIMS), "verify_aud": audience is not None}
    try:
        claims = jwt.decode(
            token,
            key,
            algorithms=[SIGNING_ALG],
            issuer=issuer,
            audience=audience,
            leeway=leeway,
            options=options,
        )
    except jwt.ExpiredSignatureError as exc:
        raise InvalidToken("expired") from exc
    except jwt.PyJWTError as exc:
        raise InvalidToken(f"rejected: {exc}") from exc

    if not isinstance(claims.get("aud"), str):
        raise InvalidToken("aud must be a single string")
    if not isinstance(claims.get("scope"), str):
        raise InvalidToken("missing scope")
    details = claims.get("authorization_details")
    if details is not None and not (
        isinstance(details, list)
        and all(isinstance(d, dict) and isinstance(d.get("type"), str) for d in details)
    ):
        raise InvalidToken("malformed authorization_details")
    return claims


class JWKSCache:
    """The issuer's public keys, cached.

    - Refreshed when older than `refresh_interval` (5 min).
    - A token with an unknown `kid` triggers an early refresh, at most once per
      `min_refetch_interval` (30 s), so new signing keys are picked up at once.
    - If the issuer is unreachable, the last good key set is used for up to
      `max_stale` (24 h).
    """

    def __init__(
        self,
        issuer: str,
        *,
        http: httpx.AsyncClient | None = None,
        jwks_uri: str | None = None,
        refresh_interval: float = 300,
        min_refetch_interval: float = 30,
        max_stale: float = 86400,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.issuer = issuer.rstrip("/")
        self._http = http or httpx.AsyncClient(timeout=5.0)
        self._owns_http = http is None
        self._jwks_uri = jwks_uri
        self._refresh_interval = refresh_interval
        self._min_refetch_interval = min_refetch_interval
        self._max_stale = max_stale
        self._clock = clock
        self._keys: dict[str, Any] | None = None
        self._fetched_at = float("-inf")
        self._last_attempt = float("-inf")
        self._lock = asyncio.Lock()

    async def get_key(self, kid: str) -> Any:
        async with self._lock:
            if self._clock() - self._fetched_at >= self._refresh_interval:
                await self._try_refresh()
            if self._keys is not None and kid not in self._keys:
                if self._clock() - self._last_attempt >= self._min_refetch_interval:
                    await self._try_refresh()

            if self._keys is None:
                raise JWKSUnavailable("issuer keys have never been fetched")
            if self._clock() - self._fetched_at > self._max_stale:
                raise JWKSUnavailable("issuer keys are too old to trust")
            key = self._keys.get(kid)
            if key is None:
                raise InvalidToken(f"unknown kid {kid!r}")
            return key

    async def _try_refresh(self) -> None:
        self._last_attempt = self._clock()
        try:
            self._keys = await self._fetch()
            self._fetched_at = self._clock()
        except (httpx.HTTPError, ValueError, KeyError, TypeError, jwt.PyJWTError) as exc:
            logger.warning("JWKS refresh from %s failed: %s", self.issuer, exc)

    async def _fetch(self) -> dict[str, Any]:
        if self._jwks_uri is None:
            meta = await self._http.get(self.issuer + METADATA_PATH)
            meta.raise_for_status()
            self._jwks_uri = meta.json()["jwks_uri"]
        response = await self._http.get(self._jwks_uri)
        response.raise_for_status()
        keys: dict[str, Any] = {}
        for jwk in response.json()["keys"]:
            if jwk.get("use", "sig") != "sig" or jwk.get("alg", SIGNING_ALG) != SIGNING_ALG:
                continue
            parsed = jwt.PyJWK(jwk)
            if parsed.key_id:
                keys[parsed.key_id] = parsed.key
        return keys

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()


class TokenVerifier:
    """Checks tokens meant for one service (`audience`) and returns a Principal."""

    def __init__(self, *, issuer: str, audience: str, jwks: JWKSCache, leeway: int = 30):
        self.issuer = issuer.rstrip("/")
        self.audience = audience
        self.jwks = jwks
        self.leeway = leeway

    async def verify(self, token: str) -> Principal:
        kid = read_kid(token)
        key = await self.jwks.get_key(kid)
        claims = decode_claims(
            token, key, issuer=self.issuer, audience=self.audience, leeway=self.leeway
        )
        return Principal.from_claims(claims, token=token)
