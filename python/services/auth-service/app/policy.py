"""policy.yaml: which clients may get which permissions, at which audiences.

Reviewed in git, no secrets. auth-service re-reads it when the file changes;
an invalid new version is logged and the previous one stays in force.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .validation import CLIENT_ID_RE, location_error, template_error

logger = logging.getLogger(__name__)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DetailTypeDef(_Strict):
    actions: list[str]
    location_syntax: Literal["prefix"] = "prefix"
    # Every location must start with one of these (e.g. the bucket names).
    location_roots: list[str] = Field(default_factory=list)


class AudienceDef(_Strict):
    scopes: list[str]
    detail_types: dict[str, DetailTypeDef] = Field(default_factory=dict)


class DetailTemplate(_Strict):
    type: str
    actions: list[str]
    locations: list[str]


class GrantDef(_Strict):
    scopes: list[str] = Field(default_factory=list)
    authorization_details: list[DetailTemplate] = Field(default_factory=list)


class ClientDef(_Strict):
    can_assert_users: bool = False
    can_relay_clients: bool = False
    can_downscope: bool = False
    grants: dict[str, GrantDef] = Field(default_factory=dict)


class Policy(_Strict):
    issuer: str
    token_ttl_seconds: int = 600
    downscoped_max_ttl_seconds: int = 3600
    max_delegation_depth: int = 3
    audiences: dict[str, AudienceDef]
    clients: dict[str, ClientDef]

    @model_validator(mode="after")
    def _check(self) -> "Policy":
        errors = self.problems()
        if errors:
            raise ValueError("invalid policy:\n  - " + "\n  - ".join(errors))
        return self

    def problems(self) -> list[str]:
        errors: list[str] = []
        if not self.issuer.startswith("https://") or self.issuer.endswith("/"):
            errors.append("issuer must be an https:// URL without a trailing '/'")
        if self.token_ttl_seconds <= 0 or self.downscoped_max_ttl_seconds <= 0:
            errors.append("token lifetimes must be positive")
        if self.max_delegation_depth < 1:
            errors.append("max_delegation_depth must be at least 1")

        for name, aud in self.audiences.items():
            where = f"audiences.{name}"
            if not CLIENT_ID_RE.match(name):
                errors.append(f"{where}: invalid audience name")
            for type_name, dtype in aud.detail_types.items():
                if not dtype.actions:
                    errors.append(f"{where}.detail_types.{type_name}: no actions")
                for root in dtype.location_roots:
                    problem = location_error(root)
                    if problem or not root.endswith("/"):
                        errors.append(
                            f"{where}.detail_types.{type_name}: bad location root {root!r}"
                        )

        for name, client in self.clients.items():
            where = f"clients.{name}"
            if not CLIENT_ID_RE.match(name):
                errors.append(f"{where}: invalid client id")
            for aud_name, grant in client.grants.items():
                gwhere = f"{where}.grants.{aud_name}"
                aud = self.audiences.get(aud_name)
                if aud is None:
                    errors.append(f"{gwhere}: unknown audience")
                    continue
                unknown = set(grant.scopes) - set(aud.scopes)
                if unknown:
                    errors.append(f"{gwhere}: unknown scopes {sorted(unknown)}")
                for i, template in enumerate(grant.authorization_details):
                    twhere = f"{gwhere}.authorization_details[{i}]"
                    dtype = aud.detail_types.get(template.type)
                    if dtype is None:
                        errors.append(f"{twhere}: unknown detail type {template.type!r}")
                        continue
                    bad_actions = set(template.actions) - set(dtype.actions)
                    if not template.actions or bad_actions:
                        errors.append(f"{twhere}: bad actions {sorted(bad_actions) or '[]'}")
                    if not template.locations:
                        errors.append(f"{twhere}: no locations")
                    for location in template.locations:
                        problem = template_error(location)
                        if problem:
                            errors.append(f"{twhere}: {problem}")
                        elif dtype.location_roots and not any(
                            location.startswith(root) for root in dtype.location_roots
                        ):
                            errors.append(f"{twhere}: {location!r} is outside location_roots")
        return errors


class PolicyStore:
    """Loads policy.yaml and reloads it when the file changes."""

    def __init__(self, path: Path):
        self.path = path
        self._mtime: float | None = None
        self.policy: Policy
        self.version: str = ""
        self._load()

    def _load(self) -> None:
        text = self.path.read_text()
        mtime = self.path.stat().st_mtime
        policy = Policy.model_validate(yaml.safe_load(text))
        self.policy = policy
        self.version = hashlib.sha256(text.encode()).hexdigest()[:12]
        self._mtime = mtime

    def current(self) -> Policy:
        try:
            mtime = self.path.stat().st_mtime
        except OSError as exc:
            logger.error("policy file unreadable, keeping version %s: %s", self.version, exc)
            return self.policy
        if mtime != self._mtime:
            try:
                self._load()
                logger.info("policy reloaded, version %s", self.version)
            except (OSError, ValueError, yaml.YAMLError) as exc:
                self._mtime = mtime  # don't retry until it changes again
                logger.error("new policy rejected, keeping version %s: %s", self.version, exc)
        return self.policy


def load_policy(path: Path) -> Policy:
    return Policy.model_validate(yaml.safe_load(path.read_text()))
