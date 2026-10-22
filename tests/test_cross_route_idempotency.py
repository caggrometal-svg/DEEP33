from __future__ import annotations

import pytest
from fastapi import HTTPException

from backend.main import (
    ChatMessage,
    ChatRequest,
    generation_request_hash,
    normalize_shared_generation_output,
    payload_hash,
)


def request_with_integer_temperature() -> ChatRequest:
    return ChatRequest(
        messages=[ChatMessage(role="user", content="Comprueba la deduplicación")],
        model=None,
        temperature=1,
        personality=None,
    )


def test_generation_hash_matches_canonical_edge_payload() -> None:
    request = request_with_integer_temperature()
    expected = payload_hash(
        {
            "messages": [{"role": "user", "content": "Comprueba la deduplicación"}],
            "model": None,
            # Pydantic parses JSON integer temperatures as floats; Edge must hash
            # the same Python-compatible JSON representation to preserve old cache keys.
            "temperature": 1.0,
            "personality": "NEUTRO",
        }
    )

    assert generation_request_hash(request, "NEUTRO") == expected
    assert generation_request_hash(request, "AGRESIVO") != expected


def test_edge_shared_record_is_normalized_for_render_replay() -> None:
    request = request_with_integer_temperature()
    output = normalize_shared_generation_output(
        {"text": "Respuesta única", "provider": "edge-primary", "model": "model-x"},
        request,
        "request-edge",
        "AGRESIVO",
    )

    assert output["request_id"] == "request-edge"
    assert output["result"]["text"] == "Respuesta única"
    assert output["result"]["provider"] == "edge-primary"
    assert output["result"]["model"] == "model-x"
    assert output["result"]["personality"] == "AGRESIVO"
    assert output["response"]["choices"][0]["message"]["content"] == "Respuesta única"
    assert output["sources"] == []


def test_render_shared_record_keeps_its_generation_payload() -> None:
    request = request_with_integer_temperature()
    output = normalize_shared_generation_output(
        {
            "request_id": "request-render",
            "latency_ms": 523,
            "result": {
                "role": "assistant",
                "text": "Respuesta render",
                "model": "model-y",
                "provider": "render",
                "personality": "NEUTRO",
            },
            "sources": [{"url": "https://example.invalid"}],
        },
        request,
        "request-edge",
        "NEUTRO",
    )

    assert output["request_id"] == "request-render"
    assert output["latency_ms"] == 523
    assert output["result"]["text"] == "Respuesta render"
    assert output["sources"] == [{
        "title": "https://example.invalid",
        "url": "https://example.invalid",
        "snippet": "",
        "provider": "unknown",
    }]
    assert output["web_navigation"] is False


def test_empty_cross_route_response_is_rejected() -> None:
    with pytest.raises(HTTPException) as raised:
        normalize_shared_generation_output(
            {"text": "  "},
            request_with_integer_temperature(),
            "request-empty",
            "NEUTRO",
        )

    assert raised.value.status_code == 502
    assert raised.value.detail == "IDEMPOTENCY_RESPONSE_INVALID"
