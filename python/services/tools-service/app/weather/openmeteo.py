"""Open-Meteo (free, no key): places by name, the weather now, daily forecasts.

Answers use the dashboard's format (flutter_ui.md "Weather feed contract"), so the agent and the
panel describe weather the same way: Celsius, km/h, and a provider-independent `condition` mapped
from WMO weather codes. Places are cached a day and weather 5 minutes (Open-Meteo's fair use).
"""

from __future__ import annotations

import re
import time
from typing import Any, Callable

import httpx

from app.registry import ToolError

# WMO weather code -> condition (flutter_ui.md). `windy` replaces a dry code when the wind is strong.
_CONDITIONS = {
    0: "clear", 1: "clear", 2: "partly_cloudy", 3: "cloudy", 45: "fog", 48: "fog",
    51: "drizzle", 53: "drizzle", 55: "drizzle", 56: "sleet", 57: "sleet", 66: "sleet", 67: "sleet",
    61: "rain", 63: "rain", 65: "rain", 80: "rain", 81: "rain", 82: "rain",
    71: "snow", 73: "snow", 75: "snow", 77: "snow", 85: "snow", 86: "snow",
    95: "thunderstorm", 96: "thunderstorm", 99: "thunderstorm",
}
_DRY = {"clear", "partly_cloudy", "cloudy", "fog"}
WINDY_KMH = 40
_CJK = re.compile(r"[㐀-鿿]")

CURRENT = "temperature_2m,apparent_temperature,relative_humidity_2m,precipitation,weather_code,wind_speed_10m,is_day"
DAILY = ("weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,precipitation_sum,"
         "wind_speed_10m_max,sunrise,sunset")


def condition(code: int | None, wind_kmh: float | None = None) -> str:
    name = _CONDITIONS.get(code, "unknown") if code is not None else "unknown"
    if name in _DRY and wind_kmh is not None and wind_kmh >= WINDY_KMH:
        return "windy"
    return name


class OpenMeteo:
    def __init__(self, http: httpx.AsyncClient, *, geocoding_url: str, forecast_url: str,
                 place_ttl: float = 86_400, weather_ttl: float = 300, clock: Callable[[], float] = time.monotonic):
        self.http, self.geocoding_url, self.forecast_url = http, geocoding_url, forecast_url
        self.place_ttl, self.weather_ttl, self.clock = place_ttl, weather_ttl, clock
        self._cache: dict[tuple, tuple[float, Any]] = {}

    async def _get(self, url: str, params: dict, ttl: float) -> dict:
        key = (url, tuple(sorted(params.items())))
        hit = self._cache.get(key)
        if hit and hit[0] > self.clock():
            return hit[1]
        try:
            r = await self.http.get(url, params=params)
        except httpx.HTTPError as e:
            raise ToolError(f"the weather service is unreachable ({type(e).__name__})") from e
        if r.status_code != 200:
            raise ToolError(f"the weather service answered {r.status_code}")
        body = r.json()
        if len(self._cache) > 2000:                     # bounded: drop what has expired, else everything
            now = self.clock()
            self._cache = {k: v for k, v in self._cache.items() if v[0] > now} if len(self._cache) < 4000 else {}
        self._cache[key] = (self.clock() + ttl, body)
        return body

    async def place(self, query: str) -> dict:
        """'Seattle', 'Seattle, US', 'Seattle, Washington', '北京': the best match, by population."""
        name, _, hint = (part.strip() for part in query.partition(","))
        if not name:
            raise ToolError("no place given", 400)
        body = await self._get(self.geocoding_url, {
            "name": name, "count": 10, "language": "zh" if _CJK.search(name) else "en", "format": "json",
        }, self.place_ttl)
        results = body.get("results") or []
        if hint:
            h = hint.casefold()
            matching = [r for r in results if h in {str(r.get(k, "")).casefold() for k in ("country_code", "country", "admin1")}]
            results = matching or results
        if not results:
            raise ToolError(f"no place called {query!r}", 404)
        r = results[0]
        return {"name": r["name"], "region": r.get("admin1"), "country": r.get("country"),
                "country_code": r.get("country_code"), "latitude": r["latitude"], "longitude": r["longitude"],
                "timezone": r.get("timezone")}

    async def current(self, query: str) -> dict:
        place = await self.place(query)
        body = await self._get(self.forecast_url, {
            "latitude": place["latitude"], "longitude": place["longitude"], "current": CURRENT, "timezone": "auto",
        }, self.weather_ttl)
        c = body.get("current") or {}
        return {"place": place, "current": {
            "time": c.get("time"),
            "temperature_c": c.get("temperature_2m"),
            "feels_like_c": c.get("apparent_temperature"),
            "condition": condition(c.get("weather_code"), c.get("wind_speed_10m")),
            "is_day": bool(c.get("is_day")),
            "humidity_pct": c.get("relative_humidity_2m"),
            "precipitation_mm": c.get("precipitation"),
            "wind_kmh": c.get("wind_speed_10m"),
        }}

    async def forecast(self, query: str, days: int) -> dict:
        place = await self.place(query)
        body = await self._get(self.forecast_url, {
            "latitude": place["latitude"], "longitude": place["longitude"], "daily": DAILY,
            "forecast_days": days, "timezone": "auto",
        }, self.weather_ttl)
        d = body.get("daily") or {}

        def col(name: str, i: int):
            values = d.get(name) or []
            return values[i] if i < len(values) else None

        out = []
        for i, date in enumerate(d.get("time") or []):
            out.append({
                "date": date,
                "condition": condition(col("weather_code", i), col("wind_speed_10m_max", i)),
                "high_c": col("temperature_2m_max", i),
                "low_c": col("temperature_2m_min", i),
                "precipitation_chance_pct": col("precipitation_probability_max", i),
                "precipitation_mm": col("precipitation_sum", i),
                "wind_max_kmh": col("wind_speed_10m_max", i),
                "sunrise": col("sunrise", i),
                "sunset": col("sunset", i),
            })
        return {"place": place, "days": out}
