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
        "source_url": url,
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
