"""The REST API: the same tools and the same check as MCP."""

import json
import logging

from tests.conftest import bearer


def test_health_needs_no_token(api):
    assert api.get("/health").json() == {"status": "healthy", "version": "test", "tools": 3}


def test_a_token_is_required(api, issuer):
    assert api.get("/v1/tools").status_code == 401
    assert api.get("/v1/weather/current", params={"place": "Seattle"}).status_code == 401
    wrong_audience = issuer.token("pi-server", ["weather/"], aud="agent-service")
    assert api.get("/v1/tools", headers=bearer(wrong_audience)).status_code == 401
    no_scope = issuer.token("pi-server", ["weather/"], scope="chat:write")
    assert api.get("/v1/tools", headers=bearer(no_scope)).status_code == 403


def test_the_list_is_what_the_token_grants(api, issuer):
    def names(locations):
        r = api.get("/v1/tools", headers=bearer(issuer.token("c", locations)))
        return [t["name"] for t in r.json()["tools"]]

    assert names(["weather/", "pi/"]) == ["weather/current", "weather/forecast", "pi/end_conversation"]
    assert names(["weather/forecast"]) == ["weather/forecast"]
    assert names(None) == []                     # no tool_access: no tools, never all


def test_weather_now(api, issuer):
    r = api.get("/v1/weather/current", params={"place": "Seattle"}, headers=bearer(issuer.token("pi-server", ["weather/"])))
    assert r.status_code == 200
    body = r.json()
    assert body["place"]["name"] == "Seattle" and body["place"]["country_code"] == "US"
    assert body["current"] == {"time": "2026-11-26T09:00", "temperature_c": 11.0, "feels_like_c": 6.6, "condition": "rain",
                               "is_day": True, "humidity_pct": 81, "precipitation_mm": 0.6, "wind_kmh": 26.8}


def test_forecast_days_and_conditions(api, issuer):
    r = api.get("/v1/weather/forecast", params={"place": "Seattle", "days": "3"},
                headers=bearer(issuer.token("pi-server", ["weather/"])))
    days = r.json()["days"]
    assert [d["condition"] for d in days] == ["rain", "windy", "clear"]     # partly cloudy at 45 km/h: windy
    assert days[0]["high_c"] == 13.1 and days[0]["precipitation_chance_pct"] == 100


def test_a_tool_not_granted_is_403_and_audited(api, issuer, caplog):
    token = issuer.token("smoke-test", ["weather/forecast"])
    with caplog.at_level(logging.INFO, logger="tools_service.audit"):
        r = api.get("/v1/weather/current", params={"place": "Seattle"}, headers=bearer(token))
    assert r.status_code == 403 and r.headers["content-type"] == "application/problem+json"
    line = json.loads(caplog.records[-1].getMessage())
    assert line["outcome"] == "denied" and line["tool"] == "weather/current" and line["sub"] == "service:smoke-test"


def test_bad_arguments_and_unknown_places(api, issuer):
    h = bearer(issuer.token("pi-server", ["weather/"]))
    assert api.get("/v1/weather/forecast", params={"place": "Seattle", "days": "9"}, headers=h).status_code == 400
    assert api.get("/v1/weather/current", headers=h).status_code == 400
    assert api.get("/v1/weather/current", params={"place": "Seattle", "extra": "x"}, headers=h).status_code == 400
    r = api.get("/v1/weather/current", params={"place": "Atlantis"}, headers=h)
    assert r.status_code == 404 and "Atlantis" in r.json()["detail"]


def test_the_weather_service_down_is_502(api, issuer, upstream):
    upstream.down = True
    r = api.get("/v1/weather/current", params={"place": "Seattle"}, headers=bearer(issuer.token("pi-server", ["weather/"])))
    assert r.status_code == 502


def test_a_relayed_caller_is_audited_with_its_actor(api, issuer, caplog):
    token = issuer.token("agent-service", ["weather/"], sub="service:pi-server", act={"sub": "service:agent-service"})
    with caplog.at_level(logging.INFO, logger="tools_service.audit"):
        assert api.get("/v1/weather/current", params={"place": "Seattle"}, headers=bearer(token)).status_code == 200
    line = json.loads(caplog.records[-1].getMessage())
    assert (line["sub"], line["act"], line["outcome"]) == ("service:pi-server", "agent-service", "ok")
    assert "place" not in caplog.records[-1].getMessage() and "Seattle" not in caplog.records[-1].getMessage()
