"""The four grants (spec: "Token endpoint") and token minting.

Grant 1  client_credentials                       a service as itself
Grant 2  token-exchange, user-id subject          start acting for a user
Grant 3  token-exchange, access-token subject     pass a user (or a relayed client) along
Grant 4  token-exchange, access-token subject     downscope one's own token
         (grant 3 vs 4 is decided by who the subject token was issued to)
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import jwt
from service_auth.constants import (
    ACCESS_TOKEN_TYP,
    SERVICE_PREFIX,
    SIGNING_ALG,
    TOKEN_TYPE_ACCESS_TOKEN,
    TOKEN_TYPE_USER_ID,
    USER_PREFIX,
)
from service_auth.errors import InvalidToken
from service_auth.verifier import decode_claims, read_kid

from .clients import ClientStore
from .errors import (
    invalid_grant,
    invalid_request,
    invalid_target,
    unauthorized_client,
)
from .keys import KeyStore
from .permissions import Permissions, narrow, parse_requested_details, resolve
from .policy import Policy, PolicyStore
from .validation import CLIENT_ID_RE, USER_ID_RE


@dataclass
class TokenRequest:
    client_id: str
    audience: str
    scopes: list[str] | None = None
    details_raw: Any = None  # parsed JSON of the authorization_details parameter
    subject_token: str | None = None
    subject_token_type: str | None = None
    expires_in: int | None = None


@dataclass
class IssuedToken:
    access_token: str
    expires_in: int
    scopes: tuple[str, ...]
    details: tuple[dict[str, Any], ...]
    issued_token_type: str | None
    # For the audit log.
    grant: str
    claims: dict[str, Any] = field(repr=False)


def _act_depth(act: Any) -> int:
    depth = 0
    while isinstance(act, dict):
        depth += 1
        act = act.get("act")
    return depth


class Issuer:
    def __init__(
        self,
        policies: PolicyStore,
        keys: KeyStore,
        clients: ClientStore,
        *,
        leeway: int = 30,
        clock: Callable[[], float] = time.time,
    ):
        self.policies = policies
        self.keys = keys
        self.clients = clients
        self.leeway = leeway
        self.clock = clock

    # ---- entry points ----------------------------------------------------------

    def client_credentials(self, req: TokenRequest) -> IssuedToken:
        """Grant 1: a token for the client itself; ${user} templates are dropped."""
        policy = self.policies.current()
        if req.expires_in is not None:
            raise invalid_request("expires_in is only supported when downscoping")
        allowed = resolve(policy, req.client_id, req.audience, {"client": req.client_id})
        perms = self._narrow(policy, req, allowed)
        now = int(self.clock())
        return self._mint(
            policy,
            grant="client_credentials",
            sub=f"{SERVICE_PREFIX}{req.client_id}",
            act=None,
            req=req,
            perms=perms,
            now=now,
            exp=now + policy.token_ttl_seconds,
            issued_token_type=None,
        )

    def token_exchange(self, req: TokenRequest) -> IssuedToken:
        policy = self.policies.current()
        if not req.subject_token or not req.subject_token_type:
            raise invalid_request("subject_token and subject_token_type are required")
        if req.subject_token_type == TOKEN_TYPE_USER_ID:
            return self._assert_user(policy, req)
        if req.subject_token_type != TOKEN_TYPE_ACCESS_TOKEN:
            raise invalid_request("unsupported subject_token_type")

        claims = self._verify_subject(policy, req.subject_token)
        if claims["aud"] == req.client_id:
            return self._pass_along(policy, req, claims)
        if claims["client_id"] == req.client_id and claims["aud"] == req.audience:
            return self._downscope(policy, req, claims)
        raise invalid_grant("subject_token was not issued to this client")

    # ---- grants 2-4 ----------------------------------------------------------------

    def _assert_user(self, policy: Policy, req: TokenRequest) -> IssuedToken:
        """Grant 2 (phase 2, non-standard): a trusted client asserts a user it authenticated."""
        if not policy.clients[req.client_id].can_assert_users:
            raise unauthorized_client("this client may not assert users")
        if req.expires_in is not None:
            raise invalid_request("expires_in is only supported when downscoping")
        subject = req.subject_token or ""
        user_id = subject[len(USER_PREFIX):] if subject.startswith(USER_PREFIX) else ""
        if not USER_ID_RE.match(user_id):
            raise invalid_request("subject_token must be 'user:<id>'")
        allowed = resolve(
            policy,
            req.client_id,
            req.audience,
            {"client": req.client_id, "user": user_id},
        )
        perms = self._narrow(policy, req, allowed)
        now = int(self.clock())
        return self._mint(
            policy,
            grant="token_exchange:user_assertion",
            sub=f"{USER_PREFIX}{user_id}",
            act={"sub": f"{SERVICE_PREFIX}{req.client_id}"},
            req=req,
            perms=perms,
            now=now,
            exp=now + policy.token_ttl_seconds,
            issued_token_type=TOKEN_TYPE_ACCESS_TOKEN,
        )

    def _pass_along(self, policy: Policy, req: TokenRequest, subject: dict) -> IssuedToken:
        """Grant 3: act for the same subject at the next service.

        - user subject: the requester's own templates, with ${user}
        - service subject (relay, api-service only): the relayed client's templates
        """
        if req.expires_in is not None:
            raise invalid_request("expires_in is only supported when downscoping")
        sub = subject["sub"]
        if sub.startswith(USER_PREFIX):
            user_id = sub[len(USER_PREFIX):]
            if not USER_ID_RE.match(user_id):
                raise invalid_grant("subject_token has an invalid user")
            owner = req.client_id
            variables = {"client": req.client_id, "user": user_id}
            grant = "token_exchange:delegation"
        elif sub.startswith(SERVICE_PREFIX):
            relayed = sub[len(SERVICE_PREFIX):]
            if not policy.clients[req.client_id].can_relay_clients:
                raise unauthorized_client("this client may not relay other clients")
            if not CLIENT_ID_RE.match(relayed) or relayed not in policy.clients:
                raise invalid_grant("subject_token's client is not in policy")
            owner = relayed
            variables = {"client": relayed}
            grant = "token_exchange:relay"
        else:
            raise invalid_grant("unsupported subject")

        depth = _act_depth(subject.get("act")) + 1
        if depth > policy.max_delegation_depth:
            raise invalid_grant("delegation chain too long")

        allowed = resolve(policy, owner, req.audience, variables)
        perms = self._narrow(policy, req, allowed)
        act: dict[str, Any] = {"sub": f"{SERVICE_PREFIX}{req.client_id}"}
        if subject.get("act"):
            act["act"] = subject["act"]
        now = int(self.clock())
        exp = min(now + policy.token_ttl_seconds, int(subject["exp"]))
        if exp <= now:
            raise invalid_grant("subject_token has expired")
        return self._mint(
            policy,
            grant=grant,
            sub=sub,
            act=act,
            req=req,
            perms=perms,
            now=now,
            exp=exp,
            issued_token_type=TOKEN_TYPE_ACCESS_TOKEN,
        )

    def _downscope(self, policy: Policy, req: TokenRequest, subject: dict) -> IssuedToken:
        """Grant 4: a strictly narrower copy of the requester's own token, same audience."""
        if not policy.clients[req.client_id].can_downscope:
            raise unauthorized_client("this client may not downscope tokens")
        allowed = Permissions(
            tuple(subject["scope"].split()),
            tuple(subject.get("authorization_details") or ()),
        )
        perms = self._narrow(policy, req, allowed)
        max_ttl = policy.downscoped_max_ttl_seconds
        ttl = max_ttl if req.expires_in is None else req.expires_in
        if ttl <= 0:
            raise invalid_request("expires_in must be positive")
        now = int(self.clock())
        return self._mint(
            policy,
            grant="token_exchange:downscope",
            sub=subject["sub"],
            act=subject.get("act"),
            req=req,
            perms=perms,
            now=now,
            exp=now + min(ttl, max_ttl),
            issued_token_type=TOKEN_TYPE_ACCESS_TOKEN,
        )

    # ---- helpers ---------------------------------------------------------------------

    def _narrow(self, policy: Policy, req: TokenRequest, allowed: Permissions) -> Permissions:
        aud = policy.audiences.get(req.audience)
        if aud is None:
            raise invalid_target(f"unknown audience {req.audience!r}")
        requested_details = None
        if req.details_raw is not None:
            requested_details = parse_requested_details(req.details_raw, aud)
        return narrow(allowed, req.scopes, requested_details)

    def _verify_subject(self, policy: Policy, token: str) -> dict[str, Any]:
        """Check a subject token issued by this server (signature, iss, exp, typ)."""
        try:
            kid = read_kid(token)
            key = self.keys.verification_key(kid)
            if key is None:
                raise InvalidToken("unknown kid")
            return decode_claims(token, key, issuer=policy.issuer, audience=None, leeway=self.leeway)
        except InvalidToken as exc:
            raise invalid_grant(f"subject_token is invalid ({exc.reason})") from None

    def _mint(
        self,
        policy: Policy,
        *,
        grant: str,
        sub: str,
        act: dict | None,
        req: TokenRequest,
        perms: Permissions,
        now: int,
        exp: int,
        issued_token_type: str | None,
    ) -> IssuedToken:
        claims: dict[str, Any] = {
            "iss": policy.issuer,
            "sub": sub,
            "aud": req.audience,
            "client_id": req.client_id,
            "scope": " ".join(perms.scopes),
            "iat": now,
            "nbf": now,
            "exp": exp,
            "jti": secrets.token_hex(16),
        }
        if act:
            claims["act"] = act
        if perms.details:
            claims["authorization_details"] = list(perms.details)
        key = self.keys.signing_key()
        token = jwt.encode(
            claims,
            key.private_key,
            algorithm=SIGNING_ALG,
            headers={"typ": ACCESS_TOKEN_TYP, "kid": key.kid},
        )
        self.clients.record_mint(req.client_id)
        return IssuedToken(
            access_token=token,
            expires_in=exp - now,
            scopes=perms.scopes,
            details=perms.details,
            issued_token_type=issued_token_type,
            grant=grant,
            claims=claims,
        )
