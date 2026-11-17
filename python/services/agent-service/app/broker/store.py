"""The login's durable copy in OpenBao (C11, model-access.md MA3): kv/services/agent-service/codex_auth.

agent-service logs in to OpenBao as role `agent-service` with an auth-service token
(aud=secrets-manager), like the deployer does. Its policy (`agent-service-login`, OpenTofu) may
read, create and update that one path and nothing else. Each OpenBao token is used for one
operation and revoked (it would expire in 10 minutes anyway).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Protocol

import httpx

logger = logging.getLogger(__name__)

PATH = "services/agent-service/codex_auth"


class StoreError(Exception):
    pass


class LoginStore(Protocol):
    async def load(self) -> str | None: ...       # None: nothing saved yet
    async def save(self, bundle: str) -> None: ...


class OpenBaoStore:
    def __init__(self, addr: str, tokens, *, role: str = "agent-service", auth_path: str = "auth-service",
                 audience: str = "secrets-manager", http: httpx.AsyncClient | None = None):
        self._base = addr.rstrip("/") + "/v1"
        self._tokens, self._role, self._auth_path, self._audience = tokens, role, auth_path, audience
        self._http = http or httpx.AsyncClient(timeout=15.0)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def load(self) -> str | None:
        async with self._session() as bao:
            r = await self._call("GET", bao)
        if r.status_code == 404:
            return None
        if r.status_code != 200:
            raise StoreError(f"reading kv/{PATH}: OpenBao answered {r.status_code}")
        try:
            return r.json()["data"]["data"]["value"]
        except (KeyError, TypeError, ValueError):
            raise StoreError(f"kv/{PATH} has no field 'value'") from None

    async def save(self, bundle: str) -> None:
        async with self._session() as bao:
            r = await self._call("POST", bao, {"data": {"value": bundle}})
        if r.status_code not in (200, 204):
            raise StoreError(f"writing kv/{PATH}: OpenBao answered {r.status_code}")

    async def _call(self, method: str, headers: dict[str, str], body: dict | None = None) -> httpx.Response:
        try:
            return await self._http.request(method, f"{self._base}/kv/data/{PATH}", headers=headers, json=body)
        except httpx.HTTPError as exc:
            raise StoreError(f"OpenBao unreachable: {exc!r}") from None

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[dict[str, str]]:
        """An OpenBao token for one operation, revoked afterwards."""
        token = await self._login()
        try:
            yield {"X-Vault-Token": token}
        finally:
            try:
                await self._http.post(f"{self._base}/auth/token/revoke-self", headers={"X-Vault-Token": token})
            except httpx.HTTPError:
                pass

    async def _login(self) -> str:
        try:
            jwt = await self._tokens.token(self._audience)
        except Exception as exc:                       # TokenRequestError, network
            raise StoreError(f"auth-service token for {self._audience}: {exc}") from None
        try:
            r = await self._http.post(f"{self._base}/auth/{self._auth_path}/login",
                                      json={"role": self._role, "jwt": jwt})
        except httpx.HTTPError as exc:
            raise StoreError(f"OpenBao unreachable: {exc!r}") from None
        if r.status_code == 503:
            raise StoreError("OpenBao is sealed")
        if r.status_code != 200:
            raise StoreError(f"OpenBao login refused ({r.status_code})")
        return r.json()["auth"]["client_token"]
