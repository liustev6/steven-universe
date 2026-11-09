"""Fixtures: a stand-in deployer behind the proxy, GitHub-shaped tokens."""

import base64
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def fake_jwt(**claims) -> str:
    """Shaped like GitHub's token (unsigned: api-service never verifies, the deployer does)."""
    def seg(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()
    base = {"iss": "https://token.actions.githubusercontent.com", "aud": "steven-universe-deployer",
            "exp": int(time.time()) + 300}
    return f"{seg({'alg': 'RS256'})}.{seg({**base, **claims})}.c2lnbmF0dXJl"


class Deployer:
    """Plays the deployer behind the proxy."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.status = 200
        self.chunks = (b'{"seq": 0, "event": "accepted"}\n', b'{"seq": 1, "event": "result", "status": "succeeded"}\n')
        self.fail = False

    def transport(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            if self.fail:
                raise httpx.ConnectError("refused")

            async def stream():
                for c in self.chunks:
                    yield c
            return httpx.Response(self.status, headers={"content-type": "application/x-ndjson"}, content=stream())
        return httpx.MockTransport(handler)


@pytest.fixture
def deployer():
    return Deployer()


@pytest.fixture
def client(deployer):
    settings = Settings(DEPLOY_RATE_BURST=3, DEPLOY_RATE_PER_MINUTE=1,
                        DEPLOY_EVENTS_RATE_BURST=5, DEPLOY_EVENTS_RATE_PER_MINUTE=1)
    with TestClient(create_app(settings, http=httpx.AsyncClient(transport=deployer.transport()))) as c:
        yield c
