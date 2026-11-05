"""
Mock weather in the shape home-hub will send (see the spec's "Weather feed
contract"). Celsius, snake_case.
"""

import random
from datetime import datetime
from typing import Any, Optional

CONDITIONS = [
    "clear",
    "partly_cloudy",
    "cloudy",
    "fog",
    "drizzle",
    "rain",
    "snow",
    "sleet",
    "thunderstorm",
    "windy",
]


class MockWeather:
    location = "San Francisco"

    def __init__(self, rng: Optional[random.Random] = None):
        self._rng = rng or random.Random()
        self.data: dict[str, Any] = {}
        self.next()

    def next(self) -> dict[str, Any]:
        """New random weather; also kept as [data]."""
        temperature = round(self._rng.uniform(-10, 35), 1)
        hour = datetime.now().hour
        self.data = {
            "location": {"name": self.location},
            "current": {
                "temperature_c": temperature,
                "condition": self._rng.choice(CONDITIONS),
                "is_day": 6 <= hour < 18,
            },
            "today": {
                "low_c": round(temperature - self._rng.randint(1, 8), 1),
                "high_c": round(temperature + self._rng.randint(1, 8), 1),
            },
        }
        return self.data
