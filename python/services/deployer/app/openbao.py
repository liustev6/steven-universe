"""A manifest's secrets, from OpenBao, with the deployer's own identity (spec "The deployer's identity").

Per deploy: an auth-service token (aud=secrets-manager, sub=service:deployer) -> OpenBao login
(role "deployer": read-only, exactly the secrets the manifests declare, 10 minutes) -> read each
declared secret -> revoke the OpenBao token. Values stay in memory and go only into the payload
for deploy-target's stdin; they are never logged.

The payload format is the same as infra/deployment/deploy.sh's (D14):
    <destination path> TAB <owner uid:gid> TAB <base64 of the formatted value> NEWLINE
"""

from __future__ import annotations

import base64
from typing import Any, Iterable, Mapping

import httpx
from service_auth import TokenClient

from app.config import Settings


class SecretsError(Exception):
    pass


def formatted(fmt: str, value: str) -> str:
    """Put the value into the manifest's `format` at the first {value} (as deploy.sh does)."""
    before, sep, after = fmt.partition("{value}")
    if not sep:
        raise SecretsError("format has no {value}")
    return f"{before}{value}{after}"


def payload_line(to: str, owner: str, text: str) -> str:
    if "\t" in to or "\n" in to or "\t" in owner or "\n" in owner:
        raise SecretsError(f"invalid destination or owner for {to!r}")
    return f"{to}\t{owner}\t{base64.b64encode(text.encode()).decode()}\n"


class OpenBaoSecrets:
    def __init__(self, settings: Settings, tokens: TokenClient, *, http: httpx.AsyncClient | None = None):
        self._s = settings
        self._tokens = tokens
        self._http = http or httpx.AsyncClient(timeout=15.0)
        self._owns_http = http is None
        self._base = settings.BAO_ADDR.rstrip("/") + "/v1"

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def payload(self, declared: Iterable[Mapping[str, Any]]) -> bytes:
        declared = list(declared)
        if not declared:
            return b""
        bao_token = await self._login()
        try:
            lines = []
            for s in declared:
                value = await self._read(bao_token, s["from"])
                lines.append(payload_line(s["to"], s.get("owner", "0:0"), formatted(s.get("format", "{value}"), value)))
            return "".join(lines).encode()
        finally:
            try:
                await self._http.post(f"{self._base}/auth/token/revoke-self", headers={"X-Vault-Token": bao_token})
            except httpx.HTTPError:
                pass  # it expires in 10 minutes anyway

    async def _login(self) -> str:
        try:
            jwt = await self._tokens.token(self._s.BAO_AUDIENCE)
        except Exception as exc:  # TokenRequestError, network
            raise SecretsError(f"auth-service token for {self._s.BAO_AUDIENCE}: {exc}") from None
        try:
            r = await self._http.post(f"{self._base}/auth/{self._s.BAO_AUTH_PATH}/login",
                                      json={"role": self._s.BAO_ROLE, "jwt": jwt})
        except httpx.HTTPError as exc:
            raise SecretsError(f"OpenBao unreachable: {exc}") from None
        if r.status_code == 503:
            raise SecretsError("OpenBao is sealed: unseal it (docs/specs/boot.md)")
        if r.status_code != 200:
            raise SecretsError(f"OpenBao login refused ({r.status_code}): {_errors(r)}")
        return r.json()["auth"]["client_token"]

    async def _read(self, bao_token: str, path: str) -> str:
        r = await self._http.get(f"{self._base}/kv/data/{path}", headers={"X-Vault-Token": bao_token})
        if r.status_code == 403:
            raise SecretsError(f"kv/{path}: not allowed. A secret newly declared in a manifest needs "
                               "`tofu apply` in infra/secrets-manager/tofu first.")
        if r.status_code == 404:
            raise SecretsError(f"kv/{path} has no value: bao kv put kv/{path} value=...")
        if r.status_code != 200:
            raise SecretsError(f"kv/{path}: OpenBao answered {r.status_code}")
        try:
            value = r.json()["data"]["data"]["value"]
        except (KeyError, TypeError, ValueError):
            raise SecretsError(f"kv/{path} has no field 'value'") from None
        if not isinstance(value, str) or not value:
            raise SecretsError(f"kv/{path}: 'value' is empty")
        return value


def _errors(r: httpx.Response) -> str:
    try:
        return "; ".join(r.json().get("errors") or []) or r.text[:200]
    except ValueError:
        return r.text[:200]
