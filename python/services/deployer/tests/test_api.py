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


def test_dockerfile_entry_point_exists():
    """The image runs `uvicorn app.main:app --factory`: that name must be a zero-argument factory."""
    import inspect
    import re
    from pathlib import Path

    from uvicorn.importer import import_from_string

    cmd = (Path(__file__).resolve().parents[1] / "Dockerfile").read_text()
    target = re.search(r'"(app\.main:\w+)", "--factory"', cmd)[1]
    factory = import_from_string(target)
    assert callable(factory) and not inspect.signature(factory).parameters


def test_reconnect_with_a_fresh_token(client, github, origin):
    sha = git(origin.path, "rev-parse", "HEAD")
    ev = events(post(client, github.token(sha), {"service": "demo", "sha": sha}))
    dep_id = ev[0]["id"]

    def get(token, after=2, ident=dep_id):
        return client.get(f"/v1/deployments/{ident}/events", params={"after": after},
                          headers={"Authorization": f"Bearer {token}"})
    r = get(github.token(sha))
    assert r.status_code == 200 and events(r) == ev[3:]                    # exactly what came after seq 2
    token = github.token(sha)
    assert get(token).status_code == 200 and get(token).status_code == 403  # each token works once
    assert get(github.token("c" * 40)).status_code == 403                   # token for another commit
    assert get(github.token(sha), ident="0" * 32).status_code == 404        # unknown deployment
    assert get(github.token(sha), ident="../../etc").status_code == 404
    assert client.get(f"/v1/deployments/{dep_id}/events").status_code == 401
