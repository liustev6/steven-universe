"""Open-Meteo: places, the condition mapping, the cache."""

import asyncio

import pytest

from app.registry import ToolError
from app.weather.openmeteo import condition


def run(coro):
    return asyncio.run(coro)


def test_conditions_follow_the_dashboard_contract():
    assert condition(0) == condition(1) == "clear"
    assert condition(2) == "partly_cloudy" and condition(3) == "cloudy"
    assert condition(66) == "sleet" and condition(95) == "thunderstorm"
    assert condition(2, 40) == "windy" and condition(61, 60) == "rain"      # rain stays rain in the wind
    assert condition(42) == "unknown" and condition(None) == "unknown"


def test_a_hint_picks_among_places_of_the_same_name(weather):
    assert run(weather.place("Portland"))["region"] == "Maine"               # the first (largest) match
    assert run(weather.place("Portland, Oregon"))["region"] == "Oregon"
    assert run(weather.place("Portland, us"))["country_code"] == "US"


def test_chinese_names(weather):
    assert run(weather.place("北京"))["country_code"] == "CN"


def test_unknown_place(weather):
    with pytest.raises(ToolError) as e:
        run(weather.place("Atlantis"))
    assert e.value.status == 404


def test_answers_are_cached(weather, upstream):
    async def twice():
        await weather.current("Seattle")
        await weather.current("Seattle")
    run(twice())
    assert upstream.calls == 2                                               # one place, one weather
