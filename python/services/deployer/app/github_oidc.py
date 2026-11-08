"""Verifying GitHub Actions OIDC tokens (deployer spec D2, D5, "Token checks (GitHub OIDC)").

GitHub signs a short-lived JWT for each workflow run that asks for one (`id-token: write`). It is
the only credential a deploy request carries: nothing is stored in GitHub. A token is accepted only
if GitHub signed it (RS256, GitHub's JWKS), it was minted for us (aud), and every claim in the
table below matches: this repository, `main`, the `production` environment (your approval), this
workflow file, a GitHub-hosted runner. Each token works once (jti).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx
import jwt
from jwt import PyJWK

from app.config import Settings

logger = logging.getLogger(__name__)

REFRESH_MIN_INTERVAL = 60.0  # at most one JWKS refetch a minute for unknown key ids


class Unauthorized(Exception):
    """Not a valid GitHub token (401)."""


class Forbidden(Exception):
    """A genuine GitHub token, but not one allowed to deploy (403)."""


class Unavailable(Exception):
    """GitHub's keys can't be fetched right now (503)."""


class GitHubVerifier:
    def __init__(self, settings: Settings, *, http: httpx.AsyncClient | None = None):
        self._s = settings
        self._jwks_url = settings.GITHUB_ISSUER.rstrip("/") + "/.well-known/jwks"
        self._http = http or httpx.AsyncClient(timeout=10.0)
        self._owns_http = http is None
        self._keys: dict[str, PyJWK] = {}
        self._fetched_at: float | None = None
        self._lock = asyncio.Lock()
        self._seen: dict[str, float] = {}  # jti -> exp: replay protection until the token expires
        self.expected = {
            "repository_id": settings.GITHUB_REPOSITORY_ID,
            "repository_owner_id": settings.GITHUB_OWNER_ID,
            "ref": settings.GITHUB_REF,
            "environment": settings.GITHUB_ENVIRONMENT,
            "job_workflow_ref": settings.GITHUB_WORKFLOW_REF,
            "runner_environment": "github-hosted",
        }

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def verify(self, token: str) -> dict[str, Any]:
        """The token's claims, or Unauthorized / Forbidden / Unavailable."""
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError:
            raise Unauthorized("malformed token") from None
        if header.get("alg") != "RS256":
            raise Unauthorized("algorithm must be RS256")
        key = await self._key(header.get("kid"))
        try:
            claims = jwt.decode(
                token,
                key.key,
                algorithms=["RS256"],
                audience=self._s.GITHUB_AUDIENCE,
                issuer=self._s.GITHUB_ISSUER,
                leeway=self._s.GITHUB_LEEWAY_SECONDS,
                options={"require": ["exp", "iat", "iss", "aud", "jti", "sha"]},
            )
        except jwt.PyJWTError as exc:
            raise Unauthorized(f"invalid token: {exc}") from None

        for claim, want in self.expected.items():
            got = claims.get(claim)
            if got is None or str(got) != want:
                raise Forbidden(f"{claim} is {got!r}, expected {want!r}")

        now = time.time()
        self._seen = {j: exp for j, exp in self._seen.items() if exp > now}
        if claims["jti"] in self._seen:
            raise Forbidden("token already used")
        self._seen[claims["jti"]] = float(claims["exp"]) + self._s.GITHUB_LEEWAY_SECONDS
        return claims

    async def _key(self, kid: str | None) -> PyJWK:
        if not kid:
            raise Unauthorized("token has no key id")
        if kid not in self._keys:
            async with self._lock:
                stale = self._fetched_at is None or time.monotonic() - self._fetched_at > REFRESH_MIN_INTERVAL
                if kid not in self._keys and stale:
                    await self._refresh()
        key = self._keys.get(kid)
        if key is None:
            raise Unauthorized("unknown signing key")
        return key

    async def _refresh(self) -> None:
        try:
            response = await self._http.get(self._jwks_url)
            response.raise_for_status()
            keys = response.json()["keys"]
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            raise Unavailable(f"cannot fetch GitHub's keys: {exc}") from None
        self._keys = {k["kid"]: PyJWK.from_dict(k) for k in keys if k.get("kty") == "RSA" and "kid" in k}
        self._fetched_at = time.monotonic()
        logger.info("GitHub JWKS: %d keys", len(self._keys))
