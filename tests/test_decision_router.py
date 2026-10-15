from datetime import datetime, timezone

from backend.decision_router import (
    DecisionRoute,
    Evidence,
    classify_query,
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
