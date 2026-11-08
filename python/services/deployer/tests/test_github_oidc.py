"""GitHub OIDC verification: every row of the spec's "Token checks" table."""

import asyncio
import time

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.github_oidc import Forbidden, GitHubVerifier, Unauthorized, Unavailable

SHA = "a" * 40


def verify(settings, github, token):
    async def go():
        async with httpx.AsyncClient(transport=github.transport()) as http:
            return await GitHubVerifier(settings, http=http).verify(token)
    return asyncio.run(go())


def test_valid_token(settings, github):
    claims = verify(settings, github, github.token(SHA))
    assert claims["sha"] == SHA and claims["actor"] == "liustev6"


@pytest.mark.parametrize("claim, value", [
    ("repository_id", "999"),                       # another repository (or a re-created one)
    ("repository_owner_id", "999"),
    ("ref", "refs/heads/feature"),                  # only main deploys
    ("environment", "staging"),                     # didn't pass the production approval
    ("environment", None),                          # no environment at all
    ("job_workflow_ref", "liustev6/steven-universe/.github/workflows/other.yml@refs/heads/main"),
    ("job_workflow_ref", "liustev6/steven-universe/.github/workflows/build.yml@refs/heads/feature"),
    ("runner_environment", "self-hosted"),
])
def test_claims_must_match(settings, github, claim, value):
    with pytest.raises(Forbidden, match=claim):
        verify(settings, github, github.token(SHA, **{claim: value}))


@pytest.mark.parametrize("overrides, match", [
    ({"aud": "someone-else"}, "(?i)audience"),
    ({"iss": "https://evil.example"}, "issuer"),
    ({"exp": int(time.time()) - 120}, "expired"),
    ({"jti": None}, "jti"),
    ({"sha": None}, "sha"),
])
def test_invalid_tokens(settings, github, overrides, match):
    with pytest.raises(Unauthorized, match=match):
        verify(settings, github, github.token(SHA, **overrides))


def test_signed_by_another_key(settings, github):
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(Unauthorized, match="Signature"):
        verify(settings, github, github.token(SHA, key=other))


def test_unknown_key_id(settings, github):
    with pytest.raises(Unauthorized, match="unknown signing key"):
        verify(settings, github, github.token(SHA, kid="not-githubs"))


def test_hs256_is_refused(settings, github):
    with pytest.raises(Unauthorized, match="RS256"):
        verify(settings, github, github.token(SHA, key="x" * 32, alg="HS256"))


def test_garbage(settings, github):
    with pytest.raises(Unauthorized):
        verify(settings, github, "not.a.jwt")


def test_replay_is_refused(settings, github):
    async def go():
        async with httpx.AsyncClient(transport=github.transport()) as http:
            v = GitHubVerifier(settings, http=http)
            token = github.token(SHA)
            await v.verify(token)
            with pytest.raises(Forbidden, match="already used"):
                await v.verify(token)
            await v.verify(github.token(SHA))  # a new token for the same commit is fine
            return github.jwks_requests
    assert asyncio.run(go()) == 1              # keys cached


def test_github_down(settings, github):
    async def go():
        transport = httpx.MockTransport(lambda r: httpx.Response(502))
        async with httpx.AsyncClient(transport=transport) as http:
            await GitHubVerifier(settings, http=http).verify(github.token(SHA))
    with pytest.raises(Unavailable):
        asyncio.run(go())
