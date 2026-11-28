"""file-management, as agent-service reaches it for its sessions (files-and-mounts.md F4, F6).

The same delegation as tools (tools.md TL3, TL4): every request carries a token exchanged for the
session's caller (RFC 8693, auth-service's grant 3): `sub` = the caller, `act` = agent-service. The
exchange asks for the profile's `files` (RFC 9396 `file_access` details, `${client}` filled in) and
gets their overlap with the caller's own grant at file-management (`grant_overlap`). What was granted
is in the token's own `authorization_details`: that, not the profile, is what the gateway acts on.
Neither token ever reaches the sandbox.

    resolve(files, owner, session_id)   the profile's request, for this caller and session
    FileManagement.grant(token, req)    at a session's start: what it was granted (or NoFiles)
    FileManagement.dav(...)             a WebDAV read forwarded with the exchanged token (the gateway's /dav)
    FileManagement.put/delete/exists    `sfsync`'s writes (the gateway's /files, F7), with the same token
"""

from __future__ import annotations

import base64
import json
import logging
from typing import Any
from urllib.parse import quote

import httpx
from service_auth import TokenClient, TokenRequestError

from app.profiles import FileAccess

logger = logging.getLogger(__name__)
AUDIENCE = "file-management"
DETAIL_TYPE = "file_access"
SCOPE_OF = {"read": "files:read", "list": "files:list", "write": "files:write", "delete": "files:delete"}
SERVICE_PREFIX = "service:"


class NoFiles(Exception):
    """The caller may reach none of what the profile asks for (no grant, or nothing in common)."""


def resolve(files: list[FileAccess], owner: str, session_id: Any = None) -> list[dict[str, Any]]:
    """The profile's request as RFC 9396 details for this caller and session. `${client}` is the
    caller's client (a `service:<client>` subject; a location naming it is left out for any other kind
    of caller); `${session}` the session's id (its own folder, F10; left out without one)."""
    client = owner[len(SERVICE_PREFIX):] if owner.startswith(SERVICE_PREFIX) else None
    session = str(session_id) if session_id else None
    out = []
    for f in files:
        locations = [loc.replace("${client}", client or "").replace("${session}", session or "")
                     for loc in f.locations
                     if (client or "${client}" not in loc) and (session or "${session}" not in loc)]
        if locations:
            out.append({"type": DETAIL_TYPE, "actions": list(f.actions), "locations": locations})
    return out


def claims_of(token: str) -> dict[str, Any]:
    """A token's claims, read (not verified): auth-service's token, just received from it over TLS."""
    try:
        payload = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError):
        return {}


def granted(token: str) -> list[dict[str, Any]]:
    """The `file_access` details a token carries: what the session may do, after the overlap."""
    return [{"type": DETAIL_TYPE, "actions": list(d.get("actions", [])), "locations": list(d.get("locations", []))}
            for d in claims_of(token).get("authorization_details") or [] if d.get("type") == DETAIL_TYPE]


def locations(details: list[dict[str, Any]], *actions: str) -> list[str]:
    """Every location the details allow one of `actions` on, sorted, without duplicates."""
    return sorted({loc for d in details if set(actions) & set(d.get("actions", [])) for loc in d.get("locations", [])})


def covers(details: list[dict[str, Any]], action: str, location: str) -> bool:
    """Whether the details allow `action` on `location` (`<bucket>/<key>`): a granted location is a
    folder (ending in '/') covering everything under it, as file-management's own prefix rule."""
    return any(location.startswith(loc) for loc in locations(details, action))


def file_instructions(details: list[dict[str, Any]], mount: str = "/workspace/inputs") -> str:
    """The session's files, for its instructions (always, when it has any; F13): from `details`, what
    the exchange GRANTED (after the intersection), never from the profile's request. The read part when
    anything is readable; the `sfsync` part only when something is writable or deletable, each line
    only for an action granted."""
    readable, writable, deletable = (locations(details, a) for a in ("read", "write", "delete"))
    parts = []
    if readable:
        lines = ["Files you can read in this session (read-only; use your shell: ls, cat, grep):"]
        lines += [f"- {mount}/{loc}" for loc in readable]
        parts.append("\n".join(lines))
    if writable or deletable:
        lines = ["To save files to storage, use the `sfsync` command in your shell; it is the only way to "
                 "write (the folders above are read-only, and files left in /workspace/work or /tmp are "
                 "lost when the session ends)."]
        if writable:
            lines.append("You may write to: " + ", ".join(writable))
        if deletable:
            lines.append("You may delete in: " + ", ".join(deletable))
        if writable:
            lines.append(f"  sfsync put /tmp/chart.png {writable[0]}chart.png")
            lines.append(f"  sfsync put /workspace/work/report.md {writable[0]}reports/report.md")
        if deletable:
            lines.append(f"  sfsync rm  {deletable[0]}chart.png")
        if writable:
            seen = [loc for loc in writable if loc in readable]
            lines.append("`put` replaces a file of the same name." + (
                f" Saved files appear under {mount}/{seen[0]} within about 2 seconds." if seen else ""))
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


class FileManagement:
    def __init__(self, tokens: TokenClient, url: str, *, http: httpx.AsyncClient | None = None):
        self.tokens, self.url = tokens, url.rstrip("/")
        self.http = http or httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0))

    async def token(self, caller_token: str, request: list[dict[str, Any]]) -> str:
        """The caller's file-management token for this request (cached by the token client until it
        nears expiry). Raises NoFiles when the caller has nothing there."""
        if not request:
            raise NoFiles("the profile asks for no files")
        scopes = sorted({SCOPE_OF[a] for d in request for a in d["actions"]})
        try:
            return await self.tokens.exchange(caller_token, AUDIENCE, scopes=scopes, details=request)
        except TokenRequestError as e:
            if e.transient:
                raise
            raise NoFiles(str(e)) from e                     # no grant, or nothing in common

    async def grant(self, caller_token: str, request: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """What a session of this caller and profile may do at file-management (or NoFiles)."""
        details = granted(await self.token(caller_token, request))
        if not details:
            raise NoFiles("the exchanged token carries no file_access")
        return details

    async def dav(self, caller_token: str, request: list[dict[str, Any]], method: str, path: str,
                  headers: dict[str, str]) -> httpx.Response:
        """A WebDAV read, forwarded with the exchanged token; the response streamed (close it)."""
        token = await self.token(caller_token, request)
        out = {k: v for k, v in headers.items() if k.lower() in ("depth", "range", "if-none-match", "content-type")}
        out["Authorization"] = f"Bearer {token}"
        req = self.http.build_request(method, f"{self.url}/dav/{path}", headers=out)
        return await self.http.send(req, stream=True)

    def _object(self, location: str) -> str:
        return f"{self.url}/objects/{quote(location, safe='/')}"

    async def put(self, caller_token: str, request: list[dict[str, Any]], location: str, body: bytes,
                  content_type: str | None) -> httpx.Response:
        """`sfsync put`: the bytes saved at `location` (`<bucket>/<key>`) with the exchanged token."""
        token = await self.token(caller_token, request)
        headers = {"Authorization": f"Bearer {token}"}
        if content_type:
            headers["Content-Type"] = content_type
        return await self.http.put(self._object(location), content=body, headers=headers)

    async def delete(self, caller_token: str, request: list[dict[str, Any]], location: str) -> httpx.Response:
        """`sfsync rm` (file-management's delete is idempotent: 204 whether or not it was there)."""
        token = await self.token(caller_token, request)
        return await self.http.delete(self._object(location), headers={"Authorization": f"Bearer {token}"})

    async def exists(self, caller_token: str, request: list[dict[str, Any]], location: str) -> bool | None:
        """Whether a file is there (a HEAD); None when that can't be told."""
        token = await self.token(caller_token, request)
        r = await self.http.head(self._object(location), headers={"Authorization": f"Bearer {token}"})
        return True if r.status_code == 200 else False if r.status_code == 404 else None

    async def aclose(self) -> None:
        await self.http.aclose()
