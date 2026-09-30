"""Weather provider abstraction + a keyless default implementation (spec §44/§108).

The *tool* knows nothing about vendors: it asks a :class:`WeatherProvider` for
normalized data. Swapping to another service means registering another
provider class and selecting it from the WebUI — no core change.

Shipped providers:

* ``open_meteo`` — free, no API key, geocoding + forecast (works out of the box);
* ``weatherapi`` — ``weatherapi.com`` style JSON API, key via credential name.

Any provider failure raises :class:`ExternalServiceError`; the tool fails over
to the next configured provider so one vendor outage is invisible to the user.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

import httpx

from app.tools.errors import (
    ExternalServiceError,
    NotFoundError,
    ToolAuthenticationError,
    ToolTimeoutError,
)

FORECAST_DAYS = 3


class WeatherProvider(ABC):
    name = ""

    def __init__(self, settings: dict[str, Any], credentials: Any = None) -> None:
        self.settings = settings or {}
        self.credentials = credentials
        self._log = logging.getLogger(f"CatooBot.Tools.Weather.{self.name}")

    @abstractmethod
    async def fetch(self, location: str, *, days: int = FORECAST_DAYS) -> dict[str, Any]:
        """Return normalized weather: {location, current, forecast[]}."""

    async def close(self) -> None:
        return None

    # ------------------------------------------------------------- helpers

    def _client(self, base_url: str, timeout: float = 8.0) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=base_url, timeout=timeout)

    @staticmethod
    async def _get_json(client: httpx.AsyncClient, url: str, params: dict[str, Any]) -> Any:
        try:
            response = await client.get(url, params=params)
        except httpx.TimeoutException as exc:
            raise ToolTimeoutError(f"weather provider timeout: {exc}") from exc
        except httpx.TransportError as exc:
            raise ExternalServiceError(
                f"weather transport error: {exc.__class__.__name__}"
            ) from exc
        if response.status_code in (401, 403):
            raise ToolAuthenticationError("weather provider rejected the API key")
        if response.status_code == 404:
            raise NotFoundError("weather provider: location not found")
        if response.status_code >= 500:
            raise ExternalServiceError(f"weather provider error {response.status_code}")
        if response.status_code != 200:
            raise ExternalServiceError(f"weather provider HTTP {response.status_code}")
        try:
            return response.json()
        except ValueError as exc:
            raise ExternalServiceError("weather provider returned invalid JSON") from exc


class OpenMeteoWeatherProvider(WeatherProvider):
    """Keyless provider: Open-Meteo geocoding + forecast."""

    name = "open_meteo"
    GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1"
    FORECAST_URL = "https://api.open-meteo.com/v1"

    async def fetch(self, location: str, *, days: int = FORECAST_DAYS) -> dict[str, Any]:
        async with self._client(self.GEOCODE_URL) as client:
            geo = await self._get_json(
                client, "/search", {"name": location, "count": 1, "language": "zh"}
            )
        results = (geo or {}).get("results") or []
        if not results:
            raise NotFoundError(f"未找到地点：{location}")
        place = results[0]
        latitude, longitude = place["latitude"], place["longitude"]
        display_name = ", ".join(
            part
            for part in (place.get("name"), place.get("admin1"), place.get("country"))
            if part
        )

        async with self._client(self.FORECAST_URL) as client:
            forecast = await self._get_json(
                client,
                "/forecast",
                {
                    "latitude": latitude,
                    "longitude": longitude,
                    "current": "temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m",
                    "daily": (
                        "weather_code,temperature_2m_max,temperature_2m_min,"
                        "precipitation_probability_max"
                    ),
                    "timezone": "auto",
                    "forecast_days": max(1, min(7, days)),
                },
            )
        return self._normalize(display_name, forecast)

    @staticmethod
    def _normalize(location: str, payload: dict[str, Any]) -> dict[str, Any]:
        current = payload.get("current") or {}
        daily = payload.get("daily") or {}
        dates = daily.get("time") or []
        codes = daily.get("weather_code") or []
        highs = daily.get("temperature_2m_max") or []
        lows = daily.get("temperature_2m_min") or []
        rains = daily.get("precipitation_probability_max") or []

        forecast: list[dict[str, Any]] = []
        for index, date in enumerate(dates):
            code = _at(codes, index)
            forecast.append(
                {
                    "date": date,
                    "condition": weather_code_text(code),
                    "high_c": _at(highs, index),
                    "low_c": _at(lows, index),
                    "precipitation_probability": _at(rains, index),
                }
            )
        return {
            "location": location,
            "current": {
                "temperature_c": current.get("temperature_2m"),
                "humidity": current.get("relative_humidity_2m"),
                "wind_kph": current.get("wind_speed_10m"),
                "condition": weather_code_text(current.get("weather_code")),
            },
            "forecast": forecast,
        }


class WeatherApiProvider(WeatherProvider):
    """Keyed provider for weatherapi.com-compatible JSON APIs."""

    name = "weatherapi"
    BASE_URL = "https://api.weatherapi.com/v1"

    async def fetch(self, location: str, *, days: int = FORECAST_DAYS) -> dict[str, Any]:
        key_name = str(self.settings.get("api_key_env") or "WEATHER_API_KEY")
        api_key = self.credentials.get_secret(key_name) if self.credentials else ""
        if not api_key:
            raise ToolAuthenticationError(
                f"weather provider '{self.name}' has no credential ({key_name})"
            )
        async with self._client(self.BASE_URL) as client:
            payload = await self._get_json(
                client,
                "/forecast.json",
                {"key": api_key, "q": location, "days": max(1, min(7, days)), "lang": "zh"},
            )
        return self._normalize(payload)

    @staticmethod
    def _normalize(payload: dict[str, Any]) -> dict[str, Any]:
        location = (payload.get("location") or {}).get("name") or "未知地点"
        current = payload.get("current") or {}
        days = ((payload.get("forecast") or {}).get("forecastday")) or []
        return {
            "location": location,
            "current": {
                "temperature_c": current.get("temp_c"),
                "humidity": current.get("humidity"),
                "wind_kph": current.get("wind_kph"),
                "condition": (current.get("condition") or {}).get("text", ""),
            },
            "forecast": [
                {
                    "date": day.get("date"),
                    "condition": ((day.get("day") or {}).get("condition") or {}).get("text", ""),
                    "high_c": (day.get("day") or {}).get("maxtemp_c"),
                    "low_c": (day.get("day") or {}).get("mintemp_c"),
                    "precipitation_probability": (day.get("day") or {}).get(
                        "daily_chance_of_rain"
                    ),
                }
                for day in days
            ],
        }


# WMO weather codes used by Open-Meteo (kept local: no vendor import in core).
_WMO_TEXT: dict[int, str] = {
    0: "晴", 1: "大致晴朗", 2: "局部多云", 3: "阴", 45: "雾", 48: "雾凇",
    51: "毛毛雨", 53: "小雨", 55: "中雨", 61: "小雨", 63: "中雨", 65: "大雨",
    66: "冻雨", 67: "冻雨", 71: "小雪", 73: "中雪", 75: "大雪", 77: "雪粒",
    80: "阵雨", 81: "较强阵雨", 82: "强阵雨", 85: "阵雪", 86: "强阵雪",
    95: "雷阵雨", 96: "雷阵雨伴冰雹", 99: "强雷暴伴冰雹",
}


def weather_code_text(code: Any) -> str:
    try:
        return _WMO_TEXT.get(int(code), "未知天气")
    except (TypeError, ValueError):
        return "未知天气"


def _at(values: list[Any], index: int) -> Any:
    return values[index] if index < len(values) else None


PROVIDERS: dict[str, Callable[[dict[str, Any], Any], WeatherProvider]] = {
    OpenMeteoWeatherProvider.name: OpenMeteoWeatherProvider,
    WeatherApiProvider.name: WeatherApiProvider,
}


def create_weather_provider(
    name: str, settings: dict[str, Any], credentials: Any = None
) -> WeatherProvider:
    factory = PROVIDERS.get(name)
    if factory is None:
        raise ExternalServiceError(f"unknown weather provider: {name}")
    return factory(settings, credentials)
