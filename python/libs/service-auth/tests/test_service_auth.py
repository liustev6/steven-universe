"""service-auth on its own: verifier attacks, JWKS cache behaviour, TokenClient
caching/retries, Principal mapping. Uses a fake issuer (httpx.MockTransport).

Run from python/services/auth-service: .venv/bin/python -m pytest ../../libs/service-auth/tests
"""

import asyncio
import json
import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from jwt.algorithms import ECAlgorithm

from service_auth import (
    InvalidToken,
    JWKSCache,
    JWKSUnavailable,
    Principal,
    TokenClient,
    TokenRequestError,
    TokenVerifier,
)

ISSUER = "https://auth.test"


class FakeIssuer:
    """Serves metadata, a JWKS, and a scripted token endpoint."""

    def __init__(self):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.kid = "k1"
        self.up = True
        self.jwks_hits = 0
        self.token_responses: list[httpx.Response] = []
        self.token_hits = 0

    def jwks(self):
        jwk = ECAlgorithm.to_jwk(self.key.public_key(), as_dict=True)
        return {"keys": [{**jwk, "kid": self.kid, "alg": "ES256", "use": "sig"}]}

    def handler(self, request: httpx.Request) -> httpx.Response:
        if not self.up:
            raise httpx.ConnectError("down")
        if request.url.path == "/.well-known/oauth-authorization-server":
            return httpx.Response(200, json={
                "issuer": ISSUER, "token_endpoint": f"{ISSUER}/oauth2/token",
                "jwks_uri": f"{ISSUER}/.well-known/jwks.json",
            })
        if request.url.path == "/.well-known/jwks.json":
            self.jwks_hits += 1
            return httpx.Response(200, json=self.jwks())
        if request.url.path == "/oauth2/token":
            self.token_hits += 1
            return self.token_responses.pop(0)
        return httpx.Response(404)

    def sign(self, claims=None, *, alg="ES256", typ="at+jwt", kid=None, key=None) -> str:
        now = int(time.time())
        payload = {
            "iss": ISSUER, "sub": "service:gpu-server", "aud": "file-management",
            "client_id": "gpu-server", "scope": "files:read", "iat": now, "nbf": now,
            "exp": now + 600, "jti": "j1",
            **(claims or {}),
        }
        return jwt.encode(payload, key or self.key, algorithm=alg,
                          headers={"typ": typ, "kid": kid or self.kid})


@pytest.fixture
def issuer():
    return FakeIssuer()


def http_for(issuer: FakeIssuer) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(issuer.handler))


def verify(issuer, token, *, audience="file-management", cache=None):
    async def run():
        async with http_for(issuer) as http:
            jwks = cache or JWKSCache(ISSUER, http=http)
            return await TokenVerifier(issuer=ISSUER, audience=audience, jwks=jwks).verify(token)
    return asyncio.run(run())


# ---- verifier -----------------------------------------------------------------------


def test_valid_token_becomes_a_principal(issuer):
    p = verify(issuer, issuer.sign())
    assert p.service == "gpu-server" and p.scopes == {"files:read"} and p.on_behalf_of is None


@pytest.mark.parametrize(
    "make",
    [
        lambda i: i.sign(typ="JWT"),                                  # not an access token
        lambda i: i.sign({"aud": "api-service"}),                     # someone else's token
        lambda i: i.sign({"iss": "https://evil.test"}),
        lambda i: i.sign({"exp": int(time.time()) - 120}),            # expired (beyond leeway)
        lambda i: i.sign(kid="unknown"),
        lambda i: i.sign(key=ec.generate_private_key(ec.SECP256R1())),  # forged signature
        lambda i: jwt.encode({"sub": "x"}, "secret", algorithm="HS256", headers={"typ": "at+jwt", "kid": "k1"}),
        lambda i: jwt.encode({"sub": "x"}, None, algorithm="none", headers={"typ": "at+jwt", "kid": "k1"}),
        lambda i: i.sign({"aud": ["file-management", "api-service"]}),  # multi-audience
        lambda i: i.sign({"client_id": None}),
        lambda i: "not.a.jwt",
    ],
)
def test_bad_tokens_are_rejected(issuer, make):
    with pytest.raises(InvalidToken):
        verify(issuer, make(issuer))


def test_delegated_token_mapping(issuer):
    token = issuer.sign({
        "sub": "user:42",
        "act": {"sub": "service:gpu-server", "act": {"sub": "service:api-service"}},
        "authorization_details": [{"type": "file_access", "actions": ["write"], "locations": ["users/42/outputs/"]}],
    })
    p = verify(issuer, token)
    assert p.service == "gpu-server"
    assert p.on_behalf_of == "user:42" and p.user_id == "42"
    assert p.actor_chain == ("gpu-server", "api-service")
    assert p.details("file_access")[0]["locations"] == ["users/42/outputs/"]
    assert p.token == token


# ---- JWKS cache ---------------------------------------------------------------------


def test_unknown_kid_triggers_one_early_refresh(issuer):
    async def run():
        now = [0.0]
        async with http_for(issuer) as http:
            cache = JWKSCache(ISSUER, http=http, clock=lambda: now[0])
            await cache.get_key("k1")
            assert issuer.jwks_hits == 1
            # key rotation: issuer publishes k2; a token with k2 arrives
            issuer.kid = "k2"
            now[0] = 31
            await cache.get_key("k2")
            assert issuer.jwks_hits == 2
            # unknown kids can't make it hammer the issuer
            with pytest.raises(InvalidToken):
                await cache.get_key("nope")
            assert issuer.jwks_hits == 2
    asyncio.run(run())


def test_keys_are_kept_while_the_issuer_is_down(issuer):
    async def run():
        now = [0.0]
        async with http_for(issuer) as http:
            cache = JWKSCache(ISSUER, http=http, clock=lambda: now[0])
            await cache.get_key("k1")
            issuer.up = False
            now[0] = 3600  # past refresh_interval: refresh fails, old keys still used
            assert await cache.get_key("k1")
            now[0] = 90_000  # past max_stale (24 h)
            with pytest.raises(JWKSUnavailable):
                await cache.get_key("k1")
    asyncio.run(run())


# ---- token client ----------------------------------------------------------------------


def _token_response(token="t1", expires_in=600):
    return httpx.Response(200, json={"access_token": token, "token_type": "Bearer", "expires_in": expires_in})


def test_token_client_caches_and_renews_early(issuer):
    async def run():
        now = [0.0]
        issuer.token_responses = [_token_response("t1"), _token_response("t2")]
        async with http_for(issuer) as http:
            client = TokenClient(ISSUER, "gpu-server", "s3cret", http=http, clock=lambda: now[0])
            assert await client.token("file-management") == "t1"
            now[0] = 500
            assert await client.token("file-management") == "t1"  # cached
            now[0] = 541  # within 60 s of expiry
            assert await client.token("file-management") == "t2"
            assert issuer.token_hits == 2
    asyncio.run(run())


def test_concurrent_callers_share_one_request(issuer):
    async def run():
        issuer.token_responses = [_token_response("t1")]
        async with http_for(issuer) as http:
            client = TokenClient(ISSUER, "gpu-server", "s3cret", http=http)
            tokens = await asyncio.gather(*[client.token("file-management") for _ in range(10)])
            assert set(tokens) == {"t1"} and issuer.token_hits == 1
    asyncio.run(run())


def test_retries_then_falls_back_to_a_still_valid_token(issuer):
    async def run():
        now = [0.0]
        sleeps = []

        async def fake_sleep(s):
            sleeps.append(s)

        issuer.token_responses = [
            _token_response("t1"),
            httpx.Response(503), httpx.Response(503), httpx.Response(503),
        ]
        async with http_for(issuer) as http:
            client = TokenClient(ISSUER, "gpu-server", "s3cret", http=http,
                                 clock=lambda: now[0], sleep=fake_sleep)
            assert await client.token("file-management") == "t1"
            now[0] = 570  # renewal due; issuer failing; t1 still valid
            assert await client.token("file-management") == "t1"
            assert sleeps == [0.2, 0.4]
            now[0] = 601  # expired: no fallback
            issuer.token_responses = [httpx.Response(503)] * 3
            with pytest.raises(TokenRequestError) as exc:
                await client.token("file-management")
            assert exc.value.transient
    asyncio.run(run())


def test_oauth_errors_are_not_retried(issuer):
    async def run():
        issuer.token_responses = [httpx.Response(400, json={"error": "invalid_scope", "error_description": "no"})]
        async with http_for(issuer) as http:
            client = TokenClient(ISSUER, "gpu-server", "s3cret", http=http)
            with pytest.raises(TokenRequestError) as exc:
                await client.token("file-management", scopes=["files:admin"])
            assert exc.value.error == "invalid_scope" and not exc.value.transient
            assert issuer.token_hits == 1
    asyncio.run(run())


def test_secret_must_come_from_a_file(monkeypatch, tmp_path):
    monkeypatch.setenv("SERVICE_AUTH_ISSUER", ISSUER)
    monkeypatch.setenv("SERVICE_AUTH_CLIENT_ID", "gpu-server")
    monkeypatch.setenv("SERVICE_AUTH_CLIENT_SECRET", "in-env")
    with pytest.raises(RuntimeError, match="not allowed"):
        TokenClient.from_env()
    monkeypatch.delenv("SERVICE_AUTH_CLIENT_SECRET")
    secret_file = tmp_path / "client_secret"
    secret_file.write_text("from-file\n")
    monkeypatch.setenv("SERVICE_AUTH_CLIENT_SECRET_FILE", str(secret_file))
    client = TokenClient.from_env()
    assert client.client_id == "gpu-server"


def test_dev_principal_only_when_local():
    from service_auth.fastapi_auth import ServiceAuth

    dev = Principal.from_dev_json(json.dumps({"service": "dev", "scopes": ["*"]}))
    with pytest.raises(RuntimeError):
        ServiceAuth(dev_principal=dev, env="production")
    assert ServiceAuth(dev_principal=dev, env="local").dev_principal.has_scopes("anything")
