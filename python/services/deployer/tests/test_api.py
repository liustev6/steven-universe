"""The HTTP API: authentication first, then the body, then the stream."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.github_oidc import GitHubVerifier
from app.main import create_app

from .conftest import git


@pytest.fixture
def client(settings, deployer, github):
    verifier = GitHubVerifier(settings, http=httpx.AsyncClient(transport=github.transport()))
    with TestClient(create_app(settings, deployer=deployer, verifier=verifier)) as c:
        yield c


def post(client, token, body):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return client.post("/v1/deployments", headers=headers, content=json.dumps(body))


def events(response):
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "healthy"


def test_deploy_streams_to_the_result(client, github, origin, fake_host):
    sha = git(origin.path, "rev-parse", "HEAD")
    r = post(client, github.token(sha), {"service": "demo", "sha": sha})
    assert r.status_code == 200 and r.headers["content-type"] == "application/x-ndjson"
    ev = events(r)
    assert ev[0]["event"] == "accepted" and ev[-1] == {**ev[-1], "event": "result", "status": "succeeded"}
    assert fake_host.calls[0][1] == sha


def test_no_token(client, origin):
    sha = git(origin.path, "rev-parse", "HEAD")
    r = post(client, None, {"service": "demo", "sha": sha})
    assert r.status_code == 401 and "Bearer" in r.headers["www-authenticate"]


def test_token_checked_before_body(client):
    # an unauthenticated caller learns nothing about the body rules
    assert post(client, "garbage", {"rm -rf": "/"}).status_code == 401


def test_wrong_environment_is_403(client, github, origin, fake_host):
    sha = git(origin.path, "rev-parse", "HEAD")
    assert post(client, github.token(sha, environment="dev"), {"service": "demo", "sha": sha}).status_code == 403
    assert not fake_host.calls


def test_sha_must_match_the_token(client, github, origin, fake_host):
    sha = git(origin.path, "rev-parse", "HEAD")
    r = post(client, github.token("c" * 40), {"service": "demo", "sha": sha})
    assert r.status_code == 422 and "sha" in r.json()["error"]
    assert not fake_host.calls


@pytest.mark.parametrize("body", [
    {"service": "demo"},                                                    # no sha
    {"service": "../etc", "sha": "SHA"},                                    # patterns
    {"service": "demo", "sha": "SHA", "host": "elsewhere"},                 # requests name, never command (D4)
    {"service": "demo", "sha": "SHA", "image_digest": "latest"},
])
def test_body_is_strict(client, github, origin, body):
    sha = git(origin.path, "rev-parse", "HEAD")
    body = {k: (sha if v == "SHA" else v) for k, v in body.items()}
    assert post(client, github.token(sha), body).status_code == 422


def test_unknown_service_is_404(client, github, origin):
    sha = git(origin.path, "rev-parse", "HEAD")
    assert post(client, github.token(sha), {"service": "nope", "sha": sha}).status_code == 404


def test_replayed_token_is_403(client, github, origin):
    sha = git(origin.path, "rev-parse", "HEAD")
    token = github.token(sha)
    assert post(client, token, {"service": "demo", "sha": sha}).status_code == 200
    assert post(client, token, {"service": "demo", "sha": sha}).status_code == 403


def test_body_size_limit(client, github, origin):
    sha = git(origin.path, "rev-parse", "HEAD")
    r = client.post("/v1/deployments", headers={"Authorization": f"Bearer {github.token(sha)}"}, content=b"x" * 5000)
    assert r.status_code == 413
