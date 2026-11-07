"""Principal: the only thing route code sees about who is calling (spec decision A1)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping

from .constants import SERVICE_PREFIX, USER_PREFIX


def _service_name(subject: str) -> str:
    return subject[len(SERVICE_PREFIX):] if subject.startswith(SERVICE_PREFIX) else subject


def _actor_chain(act: Mapping[str, Any] | None) -> tuple[str, ...]:
    """Nested RFC 8693 `act` claims, nearest actor first."""
    chain: list[str] = []
    while isinstance(act, Mapping) and isinstance(act.get("sub"), str):
        chain.append(_service_name(act["sub"]))
        act = act.get("act")
    return tuple(chain)


@dataclass(frozen=True)
class Principal:
    """Who is calling, for whom, and with which permissions.

    - `service`: the service that sent this request (the nearest actor).
    - `on_behalf_of`: the subject it acts for, `"user:42"` or a relayed
      `"service:hey-pi"`; None when the service calls as itself.
    - `authorization_details`: RFC 9396 objects, already concrete (no variables).
      Each resource service interprets its own `type`.
    """

    service: str
    scopes: frozenset[str]
    authorization_details: tuple[Mapping[str, Any], ...] = ()
    on_behalf_of: str | None = None
    actor_chain: tuple[str, ...] = ()
    client_id: str | None = None
    subject: str = ""
    token_id: str | None = None
    # The raw token, kept so a service can exchange it for the next hop (grant 3).
    token: str | None = field(default=None, repr=False, compare=False)

    @classmethod
    def from_claims(cls, claims: Mapping[str, Any], token: str | None = None) -> "Principal":
        subject = claims["sub"]
        chain = _actor_chain(claims.get("act"))
        details = tuple(MappingProxyType(dict(d)) for d in claims.get("authorization_details") or ())
        return cls(
            service=chain[0] if chain else _service_name(subject),
            scopes=frozenset(claims.get("scope", "").split()),
            authorization_details=details,
            on_behalf_of=subject if chain else None,
            actor_chain=chain,
            client_id=claims.get("client_id"),
            subject=subject,
            token_id=claims.get("jti"),
            token=token,
        )

    @classmethod
    def from_dev_json(cls, raw: str) -> "Principal":
        """Fixed principal for local development (SERVICE_AUTH_DEV_PRINCIPAL)."""
        data = json.loads(raw)
        return cls(
            service=data.get("service", "dev"),
            scopes=frozenset(data.get("scopes", [])),
            authorization_details=tuple(
                MappingProxyType(dict(d)) for d in data.get("authorization_details", [])
            ),
            on_behalf_of=data.get("on_behalf_of"),
            subject=data.get("on_behalf_of") or f"{SERVICE_PREFIX}{data.get('service', 'dev')}",
        )

    @property
    def user_id(self) -> str | None:
        """The user id if this principal acts for a user, e.g. "42"."""
        if self.on_behalf_of and self.on_behalf_of.startswith(USER_PREFIX):
            return self.on_behalf_of[len(USER_PREFIX):]
        return None

    def has_scopes(self, *scopes: str) -> bool:
        return "*" in self.scopes or all(s in self.scopes for s in scopes)

    def details(self, type_: str) -> tuple[Mapping[str, Any], ...]:
        """Authorization details of one type, e.g. `p.details("file_access")`."""
        return tuple(d for d in self.authorization_details if d.get("type") == type_)
