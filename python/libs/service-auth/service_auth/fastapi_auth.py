"""FastAPI integration: `Depends(auth.require("files:read"))` gives route code a Principal.

Install with the `fastapi` extra: `pip install service-auth[fastapi]`.
"""

from __future__ import annotations

import logging
import os
from typing import Awaitable, Callable

import httpx
from fastapi import HTTPException, Request

from .errors import InvalidToken, JWKSUnavailable
from .principal import Principal
from .verifier import JWKSCache, TokenVerifier

logger = logging.getLogger(__name__)


class ServiceAuth:
    """Authenticates requests to one service (its audience).

    ```python
    auth = ServiceAuth.from_env("file-management")

    @router.get("/objects/{bucket}/{key:path}")
    async def get_object(bucket: str, key: str, p: Principal = Depends(auth.require("files:read"))):
        ...
    ```
    """

    def __init__(
        self,
        verifier: TokenVerifier | None = None,
        *,
        dev_principal: Principal | None = None,
        env: str | None = None,
    ):
        if dev_principal is not None and env != "local":
            raise RuntimeError("a dev principal is only allowed when ENV=local")
        if verifier is None and dev_principal is None:
            raise ValueError("a verifier is required")
        self.verifier = verifier
        self.dev_principal = dev_principal

    @classmethod
    def from_env(cls, audience: str, *, http: httpx.AsyncClient | None = None) -> "ServiceAuth":
        """SERVICE_AUTH_ISSUER, or SERVICE_AUTH_DEV_PRINCIPAL (JSON) with ENV=local."""
        dev = os.environ.get("SERVICE_AUTH_DEV_PRINCIPAL")
        if dev:
            logger.warning("service-auth dev principal in use; tokens are NOT checked")
            return cls(dev_principal=Principal.from_dev_json(dev), env=os.environ.get("ENV"))
        issuer = os.environ["SERVICE_AUTH_ISSUER"]
        jwks = JWKSCache(issuer, http=http)
        return cls(TokenVerifier(issuer=issuer, audience=audience, jwks=jwks))

    async def authenticate(self, request: Request) -> Principal:
        """Return the caller's Principal, or raise 401 (RFC 6750 §3)."""
        if self.dev_principal is not None:
            return self.dev_principal
        assert self.verifier is not None
        header = request.headers.get("authorization")
        if not header:
            raise HTTPException(401, "authentication required", headers={"WWW-Authenticate": "Bearer"})
        scheme, _, token = header.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            raise _invalid_token()
        try:
            return await self.verifier.verify(token.strip())
        except InvalidToken as exc:
            logger.info("token rejected: %s", exc.reason)
            raise _invalid_token() from None
        except JWKSUnavailable as exc:
            logger.error("cannot check tokens: %s", exc)
            raise HTTPException(503, "authentication temporarily unavailable") from None

    def require(self, *scopes: str) -> Callable[[Request], Awaitable[Principal]]:
        """A dependency: authenticate, then require every scope in `scopes` (403 otherwise)."""

        async def dependency(request: Request) -> Principal:
            principal = await self.authenticate(request)
            if not principal.has_scopes(*scopes):
                logger.info(
                    "insufficient scope: %s has %s, needs %s",
                    principal.service, sorted(principal.scopes), list(scopes),
                )
                raise HTTPException(
                    403,
                    "insufficient scope",
                    headers={
                        "WWW-Authenticate": f'Bearer error="insufficient_scope", scope="{" ".join(scopes)}"'
                    },
                )
            return principal

        return dependency


def _invalid_token() -> HTTPException:
    return HTTPException(
        401, "invalid token", headers={"WWW-Authenticate": 'Bearer error="invalid_token"'}
    )
