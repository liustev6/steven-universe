"""Client credentials on disk (spec: "Client registration and credential lifecycle").

clients/<id>.sha256        hash of the current secret          (mode 600)
clients/<id>.next.sha256   hash of the next secret, while rotating
clients/<id>.json          {"enabled", "created_at", "rotated_at"}
clients/<id>.last_mint     time of the last successful token mint

Plaintext secrets are never stored here. Files are read on each check, so
changes made by the admin CLI apply immediately.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path

from .fileutil import check_private, write_private
from .validation import CLIENT_ID_RE

logger = logging.getLogger(__name__)

# Compared against when a client doesn't exist, so the timing doesn't reveal it.
_DUMMY_HASH = hashlib.sha256(b"no such client").hexdigest()


class ClientProblem(Exception):
    """An admin operation can't be done (unknown client, already exists, ...)."""


def hash_secret(secret: str) -> str:
    # The secret is 256 random bits, so a fast hash is enough; slow password
    # hashes (bcrypt, argon2) exist for guessable human passwords.
    return hashlib.sha256(secret.encode()).hexdigest()


def generate_secret() -> str:
    return secrets.token_urlsafe(32)


@dataclass(frozen=True)
class ClientRecord:
    client_id: str
    enabled: bool
    created_at: float | None
    rotated_at: float | None
    rotating: bool
    last_mint: float | None


class ClientStore:
    def __init__(self, clients_dir: Path):
        self.dir = clients_dir

    # ---- paths ---------------------------------------------------------------

    def _path(self, client_id: str, suffix: str) -> Path:
        if not CLIENT_ID_RE.match(client_id):
            raise ClientProblem(f"invalid client id {client_id!r}")
        return self.dir / f"{client_id}{suffix}"

    def _read_hash(self, path: Path) -> str | None:
        try:
            return path.read_text().strip()
        except FileNotFoundError:
            return None

    def _meta(self, client_id: str) -> dict:
        try:
            return json.loads(self._path(client_id, ".json").read_text())
        except FileNotFoundError:
            return {}

    def _write_meta(self, client_id: str, meta: dict) -> None:
        write_private(self._path(client_id, ".json"), json.dumps(meta, indent=2).encode())

    # ---- token endpoint -------------------------------------------------------

    def verify(self, client_id: str, secret: str) -> bool:
        """True if `secret` is the current or next secret of an enabled client."""
        valid_id = bool(CLIENT_ID_RE.match(client_id))
        current = self._read_hash(self.dir / f"{client_id}.sha256") if valid_id else None
        upcoming = self._read_hash(self.dir / f"{client_id}.next.sha256") if valid_id else None
        received = hash_secret(secret)
        # Constant-time comparisons; compare against a dummy hash when the client
        # has no credential, so timing doesn't reveal which clients exist.
        candidates = [h for h in (current, upcoming) if h] or [_DUMMY_HASH]
        matched = False
        for candidate in candidates:
            matched |= hmac.compare_digest(received, candidate)
        if not matched or not (current or upcoming):
            return False
        return bool(self._meta(client_id).get("enabled", False))

    def record_mint(self, client_id: str) -> None:
        try:
            write_private(self._path(client_id, ".last_mint"), str(time.time()).encode())
        except OSError as exc:  # best effort; never fail a mint for this
            logger.warning("could not record last mint for %s: %s", client_id, exc)

    # ---- admin operations (CLI) -------------------------------------------------

    def add(self, client_id: str) -> str:
        """Create a credential; returns the plaintext secret (shown once)."""
        path = self._path(client_id, ".sha256")
        if path.exists():
            raise ClientProblem(f"{client_id} already has a credential; use rotate-secret")
        secret = generate_secret()
        write_private(path, hash_secret(secret).encode(), exclusive=True)
        self._write_meta(client_id, {"enabled": True, "created_at": time.time(), "rotated_at": None})
        return secret

    def rotate(self, client_id: str) -> str:
        """Create the next secret; both work until `promote`."""
        if not self._path(client_id, ".sha256").exists():
            raise ClientProblem(f"{client_id} has no credential; use add-client")
        secret = generate_secret()
        write_private(self._path(client_id, ".next.sha256"), hash_secret(secret).encode())
        return secret

    def promote(self, client_id: str) -> None:
        upcoming = self._path(client_id, ".next.sha256")
        if not upcoming.exists():
            raise ClientProblem(f"{client_id} has no rotation in progress")
        os.replace(upcoming, self._path(client_id, ".sha256"))
        meta = self._meta(client_id)
        meta["rotated_at"] = time.time()
        self._write_meta(client_id, meta)

    def set_enabled(self, client_id: str, enabled: bool) -> None:
        if not self._path(client_id, ".sha256").exists():
            raise ClientProblem(f"{client_id} has no credential")
        meta = self._meta(client_id)
        meta["enabled"] = enabled
        self._write_meta(client_id, meta)

    def list(self) -> list[ClientRecord]:
        records = []
        for path in sorted(self.dir.glob("*.sha256")):
            if path.name.endswith(".next.sha256"):
                continue
            client_id = path.name[: -len(".sha256")]
            meta = self._meta(client_id)
            last = self._read_hash(self.dir / f"{client_id}.last_mint")
            records.append(
                ClientRecord(
                    client_id=client_id,
                    enabled=bool(meta.get("enabled", False)),
                    created_at=meta.get("created_at"),
                    rotated_at=meta.get("rotated_at"),
                    rotating=(self.dir / f"{client_id}.next.sha256").exists(),
                    last_mint=float(last) if last else None,
                )
            )
        return records

    def check_permissions(self) -> None:
        """Startup check: refuse to run with readable credential files."""
        if not self.dir.exists():
            return
        for path in self.dir.iterdir():
            if path.is_file():
                check_private(path)
