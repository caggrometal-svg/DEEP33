from datetime import datetime, timedelta, timezone

from backend.decision_router import (
    DecisionRoute,
    Evidence,
    classify_query,
    format_structured_answer,
    validate_evidence,
)


def test_classifier_routes_stable_knowledge_to_internal():
    assert classify_query("¿Cuántos huesos tiene el cuerpo humano?").route == DecisionRoute.INTERNAL


def test_classifier_routes_current_dollar_to_finance():
    decision = classify_query("¿A cuánto está el dólar hoy en Chile?")
    assert decision.route == DecisionRoute.FINANCE


def test_classifier_routes_recent_earthquakes_to_seismic():
    decision = classify_query("¿Cuántos sismos han ocurrido en los últimos 10 minutos?")
    assert decision.route == DecisionRoute.SEISMIC


def test_classifier_routes_weather_to_weather():
    assert classify_query("¿Mañana va a hacer frío?").route == DecisionRoute.WEATHER


def test_classifier_routes_explicit_web_request_to_web():
    assert classify_query("Busca en internet quién ganó la elección hoy").route == DecisionRoute.WEB


def test_validation_rejects_missing_metadata():
    evidence = Evidence(ok=True, route=DecisionRoute.FINANCE, data={"value": 900, "date": "2026-10-15"})
    validated = __import__("asyncio").run(validate_evidence(evidence))
    assert validated.ok is False
    assert validated.error == "EVIDENCE_METADATA_MISSING"


def test_validation_rejects_incomplete_finance_data():
    evidence = Evidence(
        ok=True,
        route=DecisionRoute.FINANCE,
        provider="test",
        source_url="https://example.test",
        retrieved_at=datetime.now(timezone.utc).isoformat(),
        data={"value": "900"},
    )
    validated = __import__("asyncio").run(validate_evidence(evidence))
    assert validated.ok is False



def test_forecast_weather_evidence_validates_and_formats_the_forecast_day():
    forecast_date = (datetime.now(timezone.utc) + timedelta(days=1)).date().isoformat()
    evidence = Evidence(
        ok=True,
        route=DecisionRoute.WEATHER,
        provider="Open-Meteo",
        source_url="https://api.open-meteo.com/v1/forecast",
        retrieved_at=datetime.now(timezone.utc).isoformat(),
        data={
            "forecast": True,
            "forecast_date": forecast_date,
            "temperature_min_c": 4,
            "temperature_max_c": 17,
            "weather_code": 2,
            "condition": "parcialmente nublado",
            "precipitation_probability_pct": 10,
            "location_name": "Santiago",
        },
    )
    validated = __import__("asyncio").run(validate_evidence(evidence))
    assert validated.ok is True
    answer = format_structured_answer("¿Mañana hará frío?", validated)
    assert forecast_date in answer
    assert "4 °C" in answer
    assert "17 °C" in answer


def test_finance_answer_uses_the_validated_value_verbatim():
    evidence = Evidence(
        ok=True,
        route=DecisionRoute.FINANCE,
        provider="Yahoo Finance (USD/CLP)",
        source_url="https://query1.finance.yahoo.com/v8/finance/chart/CLP=X",
        retrieved_at=datetime.now(timezone.utc).isoformat(),
        data={
            "value": 965.5,
            "market_timestamp": datetime.now(timezone.utc).isoformat(),
            "unit": "CLP/USD",
        },
    )
    validated = __import__("asyncio").run(validate_evidence(evidence))
    assert validated.ok is True
    answer = format_structured_answer("¿A cuánto está el dólar?", validated)
    assert "965,50 pesos chilenos" in answer
