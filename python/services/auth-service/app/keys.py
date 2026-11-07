"""Signing keys (spec: "Signing keys").

`keys/<kid>.pem` are ECDSA P-256 private keys (mode 600). `keys/active` holds the
kid used for signing. Every key in the directory is published in the JWKS, so a
new key can be published before it signs anything, and a retired key is simply
deleted. auth-service notices changes made by the admin CLI without a restart.
"""

from __future__ import annotations

import logging
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from jwt.algorithms import ECAlgorithm

from .fileutil import check_private, write_private

logger = logging.getLogger(__name__)

ACTIVE_FILE = "active"


class KeyProblem(Exception):
    """A key problem the admin must fix (missing, unreadable, wrong type)."""


@dataclass(frozen=True)
class SigningKey:
    kid: str
    private_key: ec.EllipticCurvePrivateKey

    @property
    def public_key(self) -> ec.EllipticCurvePublicKey:
        return self.private_key.public_key()

    def jwk(self) -> dict[str, Any]:
        jwk = ECAlgorithm.to_jwk(self.public_key, as_dict=True)
        jwk.update({"kid": self.kid, "alg": "ES256", "use": "sig"})
        return jwk


def _load_dir(keys_dir: Path) -> tuple[dict[str, SigningKey], str]:
    keys: dict[str, SigningKey] = {}
    for path in sorted(keys_dir.glob("*.pem")):
        check_private(path)
        key = serialization.load_pem_private_key(path.read_bytes(), password=None)
        if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(
            key.curve, ec.SECP256R1
        ):
            raise KeyProblem(f"{path.name} is not an ECDSA P-256 private key")
        keys[path.stem] = SigningKey(path.stem, key)
    active_path = keys_dir / ACTIVE_FILE
    if not active_path.exists():
        raise KeyProblem("no active signing key (run: auth-service-admin new-key, then promote-key)")
    active = active_path.read_text().strip()
    if active not in keys:
        raise KeyProblem(f"active key {active!r} has no keys/{active}.pem")
    return keys, active


class KeyStore:
    def __init__(self, keys_dir: Path):
        self.keys_dir = keys_dir
        self._stamp: tuple | None = None
        self._keys, self._active = _load_dir(keys_dir)
        self._stamp = self._current_stamp()

    def _current_stamp(self) -> tuple:
        active = self.keys_dir / ACTIVE_FILE
        return (
            self.keys_dir.stat().st_mtime_ns,
            active.stat().st_mtime_ns if active.exists() else None,
        )

    def _maybe_reload(self) -> None:
        try:
            stamp = self._current_stamp()
        except OSError as exc:
            logger.error("keys directory unreadable, keeping current keys: %s", exc)
            return
        if stamp == self._stamp:
            return
        self._stamp = stamp
        try:
            self._keys, self._active = _load_dir(self.keys_dir)
            logger.info("signing keys reloaded: active=%s published=%s", self._active, sorted(self._keys))
        except (OSError, ValueError, KeyProblem) as exc:
            logger.error("new key set rejected, keeping current keys: %s", exc)

    def signing_key(self) -> SigningKey:
        self._maybe_reload()
        return self._keys[self._active]

    def verification_key(self, kid: str) -> Any | None:
        self._maybe_reload()
        key = self._keys.get(kid)
        return key.public_key if key else None

    def jwks(self) -> dict[str, Any]:
        self._maybe_reload()
        return {"keys": [key.jwk() for key in self._keys.values()]}

    @property
    def active_kid(self) -> str:
        return self._active


# ---- admin operations (used by the CLI) ------------------------------------------


def new_key(keys_dir: Path) -> str:
    """Generate a key and publish it (in the JWKS) without signing with it yet."""
    keys_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    kid = f"{time.strftime('%Y-%m')}-{secrets.token_hex(2)}"
    private_key = ec.generate_private_key(ec.SECP256R1())
    pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    write_private(keys_dir / f"{kid}.pem", pem, exclusive=True)
    return kid


def promote_key(keys_dir: Path, kid: str) -> None:
    """Start signing with `kid` (it must already be published)."""
    if not (keys_dir / f"{kid}.pem").exists():
        raise KeyProblem(f"no such key {kid!r}")
    write_private(keys_dir / ACTIVE_FILE, (kid + "\n").encode())


def retire_key(keys_dir: Path, kid: str) -> None:
    """Stop publishing `kid`. Tokens it signed stop verifying."""
    active_path = keys_dir / ACTIVE_FILE
    if active_path.exists() and active_path.read_text().strip() == kid:
        raise KeyProblem(f"{kid!r} is the active signing key; promote another key first")
    path = keys_dir / f"{kid}.pem"
    if not path.exists():
        raise KeyProblem(f"no such key {kid!r}")
    os.remove(path)


def list_keys(keys_dir: Path) -> list[tuple[str, bool, float]]:
    """(kid, is_active, created) for every published key."""
    active_path = keys_dir / ACTIVE_FILE
    active = active_path.read_text().strip() if active_path.exists() else None
    return [
        (p.stem, p.stem == active, p.stat().st_mtime) for p in sorted(keys_dir.glob("*.pem"))
    ]
