"""The weather tools: weather/current and weather/forecast, the same functions behind both interfaces."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.registry import Registry, Tool
from app.weather.openmeteo import OpenMeteo

PLACE = Field(min_length=1, max_length=100, description=(
    "A city or town, optionally with its region or country after a comma: 'Seattle', 'Seattle, US', "
    "'Portland, Oregon', '北京'."))


class Current(BaseModel):
    model_config = ConfigDict(extra="forbid")
    place: str = PLACE


class Forecast(BaseModel):
    model_config = ConfigDict(extra="forbid")
    place: str = PLACE
    days: int = Field(1, ge=1, le=7, description="How many days, starting today: 1 = today only, at most 7.")


def register(registry: Registry, weather: OpenMeteo) -> None:
    async def current(args: Current) -> dict:
        return await weather.current(args.place)

    async def forecast(args: Forecast) -> dict:
        return await weather.forecast(args.place, args.days)

    registry.add(Tool(
        location="weather/current", title="Weather now", arguments=Current, run=current,
        api_path="/v1/weather/current",
        description=("The weather right now at a place: temperature and feels-like (°C), condition, "
                     "humidity, rain in the last 15 minutes (mm), wind (km/h), and the place's local time."),
    ))
    registry.add(Tool(
        location="weather/forecast", title="Weather forecast", arguments=Forecast, run=forecast,
        api_path="/v1/weather/forecast",
        description=("The daily forecast for a place, from today, for 1 to 7 days: condition, high and low "
                     "(°C), chance of rain (%), rain (mm), the strongest wind (km/h), sunrise and sunset, "
                     "in the place's local time. Use days=2 for tomorrow."),
    ))
