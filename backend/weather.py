"""Direct current-weather integration for DEEP33 GPS-aware requests.

The Android client supplies device coordinates for location-aware queries.
Weather queries use those coordinates directly instead of relying on a generic
web-search result for the city.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any

import httpx

DEFAULT_WEATHER_API_URL = "https://api.open-meteo.com/v1/forecast"
DEFAULT_GEOCODING_API_URL = "https://geocoding-api.open-meteo.com/v1/search"
DEFAULT_TIMEOUT_SECONDS = 4.0

_WEATHER_HTTP_CLIENT: httpx.AsyncClient | None = None
_WEATHER_HTTP_LOOP: asyncio.AbstractEventLoop | None = None

_WEATHER_CODE_TEXT = {
    0: "cielo despejado", 1: "mayormente despejado", 2: "parcialmente nublado",
    3: "nublado", 45: "niebla", 48: "niebla con escarcha",
    51: "llovizna ligera", 53: "llovizna moderada", 55: "llovizna intensa",
    56: "llovizna helada ligera", 57: "llovizna helada intensa",
    61: "lluvia ligera", 63: "lluvia moderada", 65: "lluvia intensa",
    66: "lluvia helada ligera", 67: "lluvia helada intensa",
    71: "nieve ligera", 73: "nieve moderada", 75: "nieve intensa",
    77: "granizo de nieve", 80: "chubascos ligeros", 81: "chubascos moderados",
    82: "chubascos intensos", 85: "chubascos de nieve ligeros",
    86: "chubascos de nieve intensos", 95: "tormenta",
    96: "tormenta con granizo ligero", 99: "tormenta con granizo intenso",
}

_GPS_RE = re.compile(
    r"UBICACIÓN GPS ACTUAL.*?\blat=([-+]?\d+(?:\.\d+)?)\s*,\s*lon=([-+]?\d+(?:\.\d+)?)\b",
    re.IGNORECASE | re.DOTALL,
)
_WEATHER_RE = re.compile(
    r"\b(?:clima|tiempo|temperatura|pron[oó]stico|pronostico|lluvia|llover|humedad|"
    r"calor|fr[ií]o|viento|tormenta|paraguas)\b",
    re.IGNORECASE,
)


def is_weather_query(query: str) -> bool:
    return _WEATHER_RE.search(" ".join(str(query or "").split())) is not None


def extract_gps_coordinates(messages: list[dict[str, Any]]) -> tuple[float, float] | None:
    for message in reversed(messages):
        if str(message.get("role", "")).lower() != "user":
            continue
        match = _GPS_RE.search(str(message.get("content", "")))
        if not match:
            continue
        latitude = float(match.group(1))
        longitude = float(match.group(2))
        if not -90.0 <= latitude <= 90.0:
            return None
        if not -180.0 <= longitude <= 180.0:
            return None
        return latitude, longitude
    return None


def _weather_description(code: Any) -> str:
    try:
        return _WEATHER_CODE_TEXT.get(int(code), f"código meteorológico {code}")
    except (TypeError, ValueError):
        return "condición meteorológica no especificada"





async def _weather_http_client() -> httpx.AsyncClient:
    global _WEATHER_HTTP_CLIENT, _WEATHER_HTTP_LOOP

    loop = asyncio.get_running_loop()
    if _WEATHER_HTTP_CLIENT is None or _WEATHER_HTTP_LOOP is not loop:
        previous = _WEATHER_HTTP_CLIENT
        _WEATHER_HTTP_CLIENT = httpx.AsyncClient(
            follow_redirects=False,
            headers={"User-Agent": "DEEP33-Weather/1.0"},
            limits=httpx.Limits(
                max_connections=8,
                max_keepalive_connections=4,
                keepalive_expiry=30.0,
            ),
        )
        _WEATHER_HTTP_LOOP = loop
        if previous is not None:
            try:
                await previous.aclose()
            except Exception:
                pass

    return _WEATHER_HTTP_CLIENT


async def fetch_current_weather(
    latitude: float,
    longitude: float,
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    if not -90.0 <= latitude <= 90.0:
        raise ValueError("WEATHER_LATITUDE_OUT_OF_RANGE")
    if not -180.0 <= longitude <= 180.0:
        raise ValueError("WEATHER_LONGITUDE_OUT_OF_RANGE")

    url = os.getenv("DEEP33_WEATHER_API_URL", DEFAULT_WEATHER_API_URL).strip() or DEFAULT_WEATHER_API_URL
    params = {
        "latitude": f"{latitude:.6f}",
        "longitude": f"{longitude:.6f}",
        "current": (
            "temperature_2m,relative_humidity_2m,apparent_temperature,"
            "precipitation,weather_code,wind_speed_10m"
        ),
        "timezone": "auto",
        "temperature_unit": "celsius",
        "wind_speed_unit": "kmh",
        "precipitation_unit": "mm",
    }
    timeout = max(1.0, min(6.0, float(timeout_seconds)))
    timeout_config = httpx.Timeout(
        connect=min(1.5, timeout),
        read=timeout,
        write=timeout,
        pool=timeout,
    )

    client = await _weather_http_client()
    response = await client.get(
        url,
        params=params,
        timeout=timeout_config,
    )
    response.raise_for_status()
    data = response.json()

    current = data.get("current")
    if not isinstance(current, dict):
        raise RuntimeError("WEATHER_CURRENT_DATA_MISSING")

    required = ("temperature_2m", "relative_humidity_2m", "weather_code", "wind_speed_10m", "time")
    if any(key not in current for key in required):
        raise RuntimeError("WEATHER_CURRENT_FIELDS_MISSING")

    return {
        "provider": "Open-Meteo",
        "source_url": str(response.request.url),
        "timezone": data.get("timezone") or "auto",
        "latitude": float(data.get("latitude", latitude)),
        "longitude": float(data.get("longitude", longitude)),
        "elevation_m": data.get("elevation"),
        "time": current.get("time"),
        "temperature_c": current.get("temperature_2m"),
        "feels_like_c": current.get("apparent_temperature"),
        "relative_humidity_pct": current.get("relative_humidity_2m"),
        "precipitation_mm": current.get("precipitation"),
        "weather_code": current.get("weather_code"),
        "condition": _weather_description(current.get("weather_code")),
        "wind_speed_kmh": current.get("wind_speed_10m"),
    }


def _named_weather_location(query: str) -> str:
    cleaned = " ".join(str(query or "").split()).strip()
    cleaned = re.sub(
        r"\b(?:clima|tiempo|temperatura|pron[oó]stico|pronostico|weather|forecast|"
        r"actual|actualmente|ahora|hoy|condiciones|meteorolog[ií]a)\b",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\b(?:en|de|del|para|por)\b", " ", cleaned, flags=re.IGNORECASE)
    return " ".join(cleaned.split()).strip(" ,;:-")


async def fetch_named_location_weather(
    query: str,
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any] | None:
    """Resolve a named place and return current weather when search providers miss."""
    if not is_weather_query(query):
        return None
    location = _named_weather_location(query)
    if len(location) < 2:
        return None

    timeout = max(1.0, min(4.0, float(timeout_seconds)))
    client = await _weather_http_client()
    geocode_url = (
        os.getenv("DEEP33_WEATHER_GEOCODING_URL", DEFAULT_GEOCODING_API_URL).strip()
        or DEFAULT_GEOCODING_API_URL
    )
    try:
        response = await client.get(
            geocode_url,
            params={"name": location, "count": "5", "language": "es", "format": "json"},
            timeout=httpx.Timeout(
                connect=min(1.2, timeout),
                read=timeout,
                write=min(1.0, timeout),
                pool=min(0.5, timeout),
            ),
        )
        response.raise_for_status()
        candidates = response.json().get("results") or []
    except (httpx.HTTPError, ValueError, TypeError, AttributeError):
        return None
    if not isinstance(candidates, list) or not candidates:
        return None

    lowered = str(query or "").casefold()
    country_hint = "CL" if "chile" in lowered else ""
    selected = next(
        (
            item for item in candidates
            if isinstance(item, dict)
            and (not country_hint or str(item.get("country_code") or "").upper() == country_hint)
        ),
        next((item for item in candidates if isinstance(item, dict)), None),
    )
    if not selected:
        return None
    try:
        latitude = float(selected["latitude"])
        longitude = float(selected["longitude"])
        weather = await fetch_current_weather(
            latitude,
            longitude,
            timeout_seconds=timeout,
        )
    except (KeyError, TypeError, ValueError, httpx.HTTPError, RuntimeError):
        return None

    weather["location_name"] = str(selected.get("name") or location)
    weather["country"] = str(selected.get("country") or "")
    weather["country_code"] = str(selected.get("country_code") or "")
    weather["admin1"] = str(selected.get("admin1") or "")
    return weather




def is_forecast_weather_query(query: str) -> bool:
    """Detect forecasts so they are never answered with current conditions."""
    value = " ".join(str(query or "").split()).casefold()
    return bool(re.search(
        r"\b(?:mañana|manana|pasado mañana|pasado manana|pron[oó]stico|pronostico|"
        r"previsi[oó]n|prevision|forecast|pr[oó]ximos?\s+d[ií]as|fin de semana|"
        r"next few days|tomorrow)\b",
        value,
    ))


async def fetch_weather_forecast(
    messages: list[dict[str, Any]],
    query: str,
    *,
    timeout_seconds: float = 2.5,
) -> dict[str, Any] | None:
    """Fetch a daily Open-Meteo forecast for tomorrow or the requested forecast day."""
    coordinates = extract_gps_coordinates(messages)
    location_name = ""
    country = ""
    admin1 = ""
    timeout = max(0.6, min(2.5, float(timeout_seconds)))
    client = await _weather_http_client()

    if coordinates is None:
        location = _named_weather_location(query)
        if len(location) < 2:
            return None
        geocode_url = (
            os.getenv("DEEP33_WEATHER_GEOCODING_URL", DEFAULT_GEOCODING_API_URL).strip()
            or DEFAULT_GEOCODING_API_URL
        )
        try:
            response = await asyncio.wait_for(
                client.get(
                    geocode_url,
                    params={"name": location, "count": "5", "language": "es", "format": "json"},
                    timeout=httpx.Timeout(
                        connect=min(0.7, timeout),
                        read=timeout,
                        write=min(0.7, timeout),
                        pool=min(0.5, timeout),
                    ),
                ),
                timeout=timeout,
            )
            response.raise_for_status()
            candidates = response.json().get("results") or []
        except (asyncio.TimeoutError, httpx.HTTPError, ValueError, TypeError, AttributeError):
            return None
        if not isinstance(candidates, list) or not candidates:
            return None
        lowered = str(query or "").casefold()
        country_hint = "CL" if "chile" in lowered else ""
        selected = next(
            (
                item for item in candidates
                if isinstance(item, dict)
                and (not country_hint or str(item.get("country_code") or "").upper() == country_hint)
            ),
            next((item for item in candidates if isinstance(item, dict)), None),
        )
        if not selected:
            return None
        try:
            coordinates = (float(selected["latitude"]), float(selected["longitude"]))
        except (KeyError, TypeError, ValueError):
            return None
        location_name = str(selected.get("name") or location)
        country = str(selected.get("country") or "")
        admin1 = str(selected.get("admin1") or "")

    latitude, longitude = coordinates
    lowered = str(query or "").casefold()
    day_offset = 1 if re.search(r"\b(?:mañana|manana|tomorrow)\b", lowered) else 0
    if re.search(r"\b(?:pasado mañana|pasado manana|day after tomorrow)\b", lowered):
        day_offset = 2

    forecast_url = os.getenv("DEEP33_WEATHER_API_URL", DEFAULT_WEATHER_API_URL).strip() or DEFAULT_WEATHER_API_URL
    params = {
        "latitude": f"{latitude:.6f}",
        "longitude": f"{longitude:.6f}",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,precipitation_sum",
        "forecast_days": str(max(3, day_offset + 1)),
        "timezone": "auto",
        "temperature_unit": "celsius",
        "wind_speed_unit": "kmh",
        "precipitation_unit": "mm",
    }
    try:
        response = await asyncio.wait_for(
            client.get(
                forecast_url,
                params=params,
                timeout=httpx.Timeout(
                    connect=min(0.7, timeout),
                    read=timeout,
                    write=min(0.7, timeout),
                    pool=min(0.5, timeout),
                ),
            ),
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
        daily = payload.get("daily")
        times = daily.get("time") if isinstance(daily, dict) else None
        if not isinstance(times, list) or len(times) <= day_offset:
            return None
        required_keys = (
            "temperature_2m_min",
            "temperature_2m_max",
            "weather_code",
            "precipitation_probability_max",
            "precipitation_sum",
        )
        if any(not isinstance(daily.get(key), list) or len(daily[key]) <= day_offset for key in required_keys):
            return None
        low = daily["temperature_2m_min"][day_offset]
        high = daily["temperature_2m_max"][day_offset]
        code = daily["weather_code"][day_offset]
        if (
            not isinstance(low, (int, float))
            or not isinstance(high, (int, float))
            or not isinstance(code, (int, float))
            or low > high
        ):
            return None
        return {
            "provider": "Open-Meteo",
            "source_url": str(response.request.url),
            "forecast": True,
            "forecast_date": str(times[day_offset]),
            "forecast_day_offset": day_offset,
            "timezone": str(payload.get("timezone") or "auto"),
            "latitude": float(payload.get("latitude", latitude)),
            "longitude": float(payload.get("longitude", longitude)),
            "location_name": location_name,
            "country": country,
            "admin1": admin1,
            "temperature_min_c": float(low),
            "temperature_max_c": float(high),
            "weather_code": int(code),
            "condition": _weather_description(code),
            "precipitation_probability_pct": daily["precipitation_probability_max"][day_offset],
            "precipitation_sum_mm": daily["precipitation_sum"][day_offset],
        }
    except (asyncio.TimeoutError, httpx.HTTPError, ValueError, TypeError, AttributeError):
        return None

def build_weather_evidence(messages: list[dict[str, Any]], weather: dict[str, Any]) -> str:
    query = next(
        (
            str(message.get("content", "")).strip()
            for message in reversed(messages)
            if str(message.get("role", "")).lower() == "user"
            and str(message.get("content", "")).strip()
        ),
        "",
    )
    return (
        "DATOS METEOROLÓGICOS ACTUALES OBTENIDOS DIRECTAMENTE DESDE UN SERVICIO "
        "METEOROLÓGICO SERVER-SIDE PARA LAS COORDENADAS GPS DEL DISPOSITIVO. "
        "Úsalos como datos factuales de esta consulta. No inventes valores ni digas que "
        "son una medición física del teléfono. Responde de forma natural y proporcional.\n"
        f"Consulta original: {query}\n"
        + json.dumps(weather, ensure_ascii=False, separators=(",", ":"))
    )


async def resolve_gps_weather(
    messages: list[dict[str, Any]],
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any] | None:
    query = next(
        (
            str(message.get("content", "")).strip()
            for message in reversed(messages)
            if str(message.get("role", "")).lower() == "user"
            and str(message.get("content", "")).strip()
        ),
        "",
    )
    if not is_weather_query(query):
        return None
    coordinates = extract_gps_coordinates(messages)
    if coordinates is None:
        return None
    try:
        return await fetch_current_weather(
            coordinates[0], coordinates[1], timeout_seconds=timeout_seconds
        )
    except (httpx.HTTPError, RuntimeError, ValueError, TypeError):
        # Generic web search remains the fallback and transport is untouched.
        return None
