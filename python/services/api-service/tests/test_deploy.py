"""/deploy/*: pre-checks at the edge, unchanged forwarding, streaming, reconnects."""

import json
import time

import pytest

from .conftest import fake_jwt

DEP_ID = "a" * 32


def deploy(client, token="VALID", body=None):
    token = fake_jwt() if token == "VALID" else token
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return client.post("/deploy/v1/deployments", headers=headers,
                       content=json.dumps(body or {"service": "file-management", "sha": "a" * 40}))


def reconnect(client, ident=DEP_ID, after="3", token="VALID"):
    token = fake_jwt() if token == "VALID" else token
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return client.get(f"/deploy/v1/deployments/{ident}/events", params={"after": after}, headers=headers)


def test_forwards_unchanged_and_streams_back(client, deployer):
    token = fake_jwt(sha="a" * 40)
    r = deploy(client, token, {"service": "file-management", "sha": "a" * 40})
    assert r.status_code == 200 and r.headers["content-type"] == "application/x-ndjson"
    assert [json.loads(line)["event"] for line in r.text.splitlines()] == ["accepted", "result"]
    sent = deployer.requests[0]
    assert str(sent.url) == "https://deploy.lan.liustev6.ca/v1/deployments"
    assert sent.headers["authorization"] == f"Bearer {token}"           # unchanged: the deployer verifies it
    assert json.loads(sent.content) == {"service": "file-management", "sha": "a" * 40}
    assert "cookie" not in sent.headers


def test_reconnect_is_forwarded(client, deployer):
    r = reconnect(client)
    assert r.status_code == 200 and len(r.text.splitlines()) == 2
    sent = deployer.requests[0]
    assert sent.method == "GET" and str(sent.url) == f"https://deploy.lan.liustev6.ca/v1/deployments/{DEP_ID}/events?after=3"
    assert not sent.content


@pytest.mark.parametrize("ident, after", [("../../etc", "3"), ("A" * 32, "3"), (DEP_ID, "-5"), (DEP_ID, "x")])
def test_reconnect_rejects_odd_paths(client, deployer, ident, after):
    assert reconnect(client, ident, after).status_code in (404, 422) and not deployer.requests


def test_deployer_errors_pass_through(deployer, client):
    deployer.status, deployer.chunks = 403, (b'{"error": "environment is \'dev\'"}',)
    r = deploy(client)
    assert r.status_code == 403 and "environment" in r.json()["error"]


@pytest.mark.parametrize("token, reason", [
    (None, "bearer token is required"),
    ("abc", "malformed"),
    ("a.%%%.c", "malformed"),
    (fake_jwt(iss="https://accounts.google.com"), "not a GitHub Actions token"),
    (fake_jwt(aud="sts.amazonaws.com"), "not issued for the deployer"),
    (fake_jwt(exp=int(time.time()) - 600), "expired"),
])
def test_prechecks_stop_junk_at_the_edge(client, deployer, token, reason):
    for r in (deploy(client, token), reconnect(client, token=token)):
        assert r.status_code == 401 and reason in r.json()["message"]
    assert not deployer.requests                                         # never reached the LAN


def test_body_limit(client, deployer):
    r = client.post("/deploy/v1/deployments", headers={"Authorization": f"Bearer {fake_jwt()}"}, content=b"x" * 5000)
    assert r.status_code == 413 and not deployer.requests


def test_rate_limits_are_separate(client):
    assert [deploy(client, None).status_code for _ in range(4)] == [401, 401, 401, 429]
    assert [reconnect(client, token=None).status_code for _ in range(6)] == [401] * 5 + [429]


def test_deployer_down_is_502(deployer, client):
    deployer.fail = True
    r = deploy(client)
    assert r.status_code == 502 and r.json()["message"] == "the deployer is unavailable"
