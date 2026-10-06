import asyncio

import pytest

from backend import weather as weather_module
from backend.weather import (
    build_weather_evidence,
    extract_gps_coordinates,
    is_weather_query,
    resolve_gps_weather,
)


def test_weather_query_detection():
    assert is_weather_query("¿Qué temperatura hace ahora?")
    assert is_weather_query("Dime el pronóstico de hoy")
    assert not is_weather_query("¿Quién escribió Dune?")


def test_extract_gps_coordinates_from_deep33_marker():
    messages = [{
        "role": "user",
        "content": (
            "¿Qué clima hace ahora?\n\n"
            "[UBICACIÓN GPS ACTUAL — uso interno para consultas locales: "
            "Santiago, Chile; lat=-33.448900, lon=-70.669300]"
        ),
    }]
    assert extract_gps_coordinates(messages) == pytest.approx((-33.4489, -70.6693))


def test_resolve_gps_weather_uses_coordinates(monkeypatch):
    calls = {}

    async def fake_fetch(latitude, longitude, *, timeout_seconds):
        calls["latitude"] = latitude
        calls["longitude"] = longitude
        calls["timeout_seconds"] = timeout_seconds
        return {
            "provider": "Open-Meteo",
            "time": "2026-10-06T03:00",
            "temperature_c": 12.3,
            "condition": "parcialmente nublado",
        }

    monkeypatch.setattr("backend.weather.fetch_current_weather", fake_fetch)

    result = asyncio.run(resolve_gps_weather([{
        "role": "user",
        "content": (
            "clima ahora\n"
            "[UBICACIÓN GPS ACTUAL — uso interno para consultas locales: "
            "Santiago; lat=-33.448900, lon=-70.669300]"
        ),
    }]))

    assert result is not None
    assert calls["latitude"] == pytest.approx(-33.4489)
    assert calls["longitude"] == pytest.approx(-70.6693)
    assert calls["timeout_seconds"] == 4.0


def test_build_weather_evidence_contains_current_values():
    evidence = build_weather_evidence(
        [{"role": "user", "content": "¿Qué temperatura hace?"}],
        {
            "provider": "Open-Meteo",
            "time": "2026-10-06T03:00",
            "temperature_c": 12.3,
            "condition": "parcialmente nublado",
        },
    )
    assert "12.3" in evidence
    assert "parcialmente nublado" in evidence



def test_weather_http_client_reuses_connection_pool():
    async def exercise():
        first = await weather_module._weather_http_client()
        second = await weather_module._weather_http_client()
        assert first is second
        await first.aclose()
        weather_module._WEATHER_HTTP_CLIENT = None
        weather_module._WEATHER_HTTP_LOOP = None

    asyncio.run(exercise())
