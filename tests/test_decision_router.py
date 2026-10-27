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


def test_classifier_routes_external_mutable_facts_to_web_without_magic_words():
    cases = (
        "¿Quién es el CEO de NVIDIA?",
        "¿Cuál es la versión estable de Python?",
        "¿Cuál es el horario del Museo del Prado?",
        "¿Qué dice la documentación de FastAPI sobre lifespan?",
        "¿Cuál es la normativa de equipaje de LATAM?",
        "¿Quién fundó OpenAI?",
        "¿Cuál es la población de Japón?",
    )
    for query in cases:
        assert classify_query(query).route == DecisionRoute.WEB, query


def test_classifier_keeps_stable_explanations_internal():
    cases = (
        "¿Qué es la fotosíntesis?",
        "Explica cómo funciona la recursión",
        "¿Por qué el cielo se ve azul?",
    )
    for query in cases:
        assert classify_query(query).route == DecisionRoute.INTERNAL, query


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



def test_validation_rejects_inverted_usd_clp_quote():
    evidence = Evidence(
        ok=True,
        route=DecisionRoute.FINANCE,
        provider="Yahoo Finance (USD/CLP)",
        source_url="https://query1.finance.yahoo.com/v8/finance/chart/CLP=X",
        retrieved_at=datetime.now(timezone.utc).isoformat(),
        data={
            "value": 0.001,
            "market_timestamp": datetime.now(timezone.utc).isoformat(),
            "unit": "CLP/USD",
        },
    )
    validated = __import__("asyncio").run(validate_evidence(evidence))
    assert validated.ok is False
    assert validated.error == "FINANCE_VALUE_OUT_OF_RANGE"


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
    assert "Fuentes consultadas:" in answer
    assert "https://query1.finance.yahoo.com/v8/finance/chart/CLP=X" in answer
