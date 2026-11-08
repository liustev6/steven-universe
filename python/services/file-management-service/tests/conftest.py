"""Test helpers: a fake issuer that signs real ES256 tokens and serves its JWKS, an app wired to
it with in-memory storage, and token presets matching auth-service's deployed policy."""

from __future__ import annotations

import time
import uuid

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from jwt.algorithms import ECAlgorithm
from service_auth.fastapi_auth import ServiceAuth
from service_auth.verifier import JWKSCache, TokenVerifier

from app.config import Settings
from app.main import create_app
from app.storage.memory import MemoryStorage

ISSUER = "https://auth.test"
ALL = ["files:read", "files:write", "files:delete", "files:list"]


class FakeIssuer:
    def __init__(self):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.kid = "k1"

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/.well-known/oauth-authorization-server":
            return httpx.Response(200, json={"issuer": ISSUER, "jwks_uri": f"{ISSUER}/.well-known/jwks.json"})
        if request.url.path == "/.well-known/jwks.json":
            jwk = ECAlgorithm.to_jwk(self.key.public_key(), as_dict=True)
            return httpx.Response(200, json={"keys": [{**jwk, "kid": self.kid, "alg": "ES256", "use": "sig"}]})
        return httpx.Response(404)

    def token(self, client: str, scopes: list[str], locations: dict[str, list[str]] | None = None, *,
              aud: str = "file-management", sub: str | None = None, act: dict | None = None,
              key=None) -> str:
        now = int(time.time())
        claims = {
            "iss": ISSUER, "sub": sub or f"service:{client}", "aud": aud, "client_id": client,
            "scope": " ".join(scopes), "iat": now, "nbf": now, "exp": now + 600, "jti": uuid.uuid4().hex,
        }
        if locations:
            claims["authorization_details"] = [
                {"type": "file_access", "actions": actions, "locations": [loc]}
                for loc, actions in locations.items()
            ]
        if act:
            claims["act"] = act
        return jwt.encode(claims, key or self.key, algorithm="ES256", headers={"typ": "at+jwt", "kid": self.kid})


@pytest.fixture
def issuer() -> FakeIssuer:
    return FakeIssuer()


@pytest.fixture
def settings() -> Settings:
    return Settings(MAX_UPLOAD_BYTES=1024, LIST_MAX_KEYS=1000)


@pytest.fixture
def storage(settings) -> MemoryStorage:
    return MemoryStorage(settings.BUCKETS)


@pytest.fixture
def client(issuer, settings, storage):
    http = httpx.AsyncClient(transport=httpx.MockTransport(issuer.handler))
    auth = ServiceAuth(TokenVerifier(issuer=ISSUER, audience="file-management", jwks=JWKSCache(ISSUER, http=http)))
    with TestClient(create_app(settings, storage=storage, auth=auth)) as c:
        yield c


# Tokens as auth-service's deployed policy issues them.
@pytest.fixture
def pi(issuer):
    """pi-server: everything on its own folder."""
    return {"Authorization": "Bearer " + issuer.token(
        "pi-server", ALL, {"private/pi-server/": ["read", "write", "delete", "list"]})}


@pytest.fixture
def api(issuer):
    """api-service's own token: what anonymous visitors may do."""
    return {"Authorization": "Bearer " + issuer.token(
        "api-service", ["files:read", "files:list"], {"public/": ["read", "list"]})}


@pytest.fixture
def relayed(issuer):
    """api-service relaying pi-server (grant 3): pi-server's grants, api-service as the actor."""
    return {"Authorization": "Bearer " + issuer.token(
        "api-service", ALL, {"private/pi-server/": ["read", "write", "delete", "list"]},
        sub="service:pi-server", act={"sub": "service:api-service"})}
