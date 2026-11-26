"""Test helpers: a fake issuer signing real ES256 tokens (with tool_access details, as auth-service's
policy issues them), a fake Open-Meteo, and tools-service's two apps wired to both."""

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

from app import pi_tools
from app.api import create_api
from app.mcp_server import create_mcp
from app.registry import Registry
from app.weather import tools as weather_tools
from app.weather.openmeteo import OpenMeteo

ISSUER = "https://auth.test"
MCP_URL = "http://testserver/mcp"


class FakeIssuer:
    def __init__(self):
        self.key = ec.generate_private_key(ec.SECP256R1())

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/.well-known/oauth-authorization-server":
            return httpx.Response(200, json={"issuer": ISSUER, "jwks_uri": f"{ISSUER}/.well-known/jwks.json"})
        if request.url.path == "/.well-known/jwks.json":
            jwk = ECAlgorithm.to_jwk(self.key.public_key(), as_dict=True)
            return httpx.Response(200, json={"keys": [{**jwk, "kid": "k1", "alg": "ES256", "use": "sig"}]})
        return httpx.Response(404)

    def token(self, client: str, locations: list[str] | None, *, scope: str = "tools:call", aud: str = "tools-service",
              sub: str | None = None, act: dict | None = None) -> str:
        now = int(time.time())
        claims = {"iss": ISSUER, "sub": sub or f"service:{client}", "aud": aud, "client_id": client, "scope": scope,
                  "iat": now, "nbf": now, "exp": now + 600, "jti": uuid.uuid4().hex}
        if locations is not None:
            claims["authorization_details"] = [{"type": "tool_access", "actions": ["call"], "locations": locations}]
        if act:
            claims["act"] = act
        return jwt.encode(claims, self.key, algorithm="ES256", headers={"typ": "at+jwt", "kid": "k1"})


class FakeOpenMeteo:
    """Seattle, 北京 and nothing else; counts upstream calls (for the cache)."""

    def __init__(self):
        self.calls = 0
        self.down = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        if self.down:
            return httpx.Response(503)
        q = request.url.params
        if request.url.host == "geocoding.test":
            places = {
                "Seattle": [{"name": "Seattle", "admin1": "Washington", "country": "United States", "country_code": "US",
                             "latitude": 47.6, "longitude": -122.3, "timezone": "America/Los_Angeles"}],
                "Portland": [{"name": "Portland", "admin1": "Maine", "country": "United States", "country_code": "US",
                              "latitude": 43.7, "longitude": -70.3, "timezone": "America/New_York"},
                             {"name": "Portland", "admin1": "Oregon", "country": "United States", "country_code": "US",
                              "latitude": 45.5, "longitude": -122.7, "timezone": "America/Los_Angeles"}],
                "北京": [{"name": "北京", "admin1": "北京市", "country": "中国", "country_code": "CN",
                         "latitude": 39.9, "longitude": 116.4, "timezone": "Asia/Shanghai"}],
            }
            found = places.get(q["name"])
            return httpx.Response(200, json={"results": found} if found else {"generationtime_ms": 0.1})
        if "current" in q:
            return httpx.Response(200, json={"current": {
                "time": "2026-11-26T09:00", "temperature_2m": 11.0, "apparent_temperature": 6.6,
                "relative_humidity_2m": 81, "precipitation": 0.6, "weather_code": 61, "wind_speed_10m": 26.8, "is_day": 1}})
        days = int(q["forecast_days"])
        return httpx.Response(200, json={"daily": {
            "time": [f"2026-11-{26 + i}" for i in range(days)], "weather_code": [63, 2, 0][:days],
            "temperature_2m_max": [13.1, 8.9, 7.0][:days], "temperature_2m_min": [8.7, 7.8, 2.0][:days],
            "precipitation_probability_max": [100, 20, 0][:days], "precipitation_sum": [16.6, 0.2, 0.0][:days],
            "wind_speed_10m_max": [29.2, 45.0, 10.0][:days],
            "sunrise": ["2026-11-26T07:30"] * days, "sunset": ["2026-11-26T16:22"] * days}})


@pytest.fixture
def issuer() -> FakeIssuer:
    return FakeIssuer()


@pytest.fixture
def upstream() -> FakeOpenMeteo:
    return FakeOpenMeteo()


@pytest.fixture
def weather(upstream) -> OpenMeteo:
    http = httpx.AsyncClient(transport=httpx.MockTransport(upstream.handler))
    return OpenMeteo(http, geocoding_url="https://geocoding.test/v1/search", forecast_url="https://forecast.test/v1/forecast")


@pytest.fixture
def registry(weather) -> Registry:
    r = Registry()
    weather_tools.register(r, weather)
    pi_tools.register(r)
    return r


@pytest.fixture
def verifier(issuer) -> TokenVerifier:
    http = httpx.AsyncClient(transport=httpx.MockTransport(issuer.handler))
    return TokenVerifier(issuer=ISSUER, audience="tools-service", jwks=JWKSCache(ISSUER, http=http))


@pytest.fixture
def api(registry, verifier):
    with TestClient(create_api(registry, ServiceAuth(verifier), revision="test")) as c:
        yield c


@pytest.fixture
def mcp(registry, verifier):
    return create_mcp(registry, verifier, issuer=ISSUER, resource_url=MCP_URL, allowed_hosts=["testserver"],
                      revision="test")


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}
