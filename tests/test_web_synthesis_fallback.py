from __future__ import annotations

import asyncio
import json

import backend.main as main
from backend.main import (
    _compact_web_synthesis_payload,
    _extractive_web_evidence_fallback,
    _normalize_web_search_query,
)


def evidence_payload() -> dict:
    evidence = {
        "query": "Carlos Muñoz Ferrada",
        "fetched_pages": [
            {
                "title": "Fuente histórica",
                "url": "https://example.org/history",
                "domain": "example.org",
                "provider": "provider-a",
                "text": (
                    "Esta frase pertenece literalmente a una página de origen y "
                    "no debe convertirse en la respuesta final de DEEP33."
                ),
            },
            {
                "title": "Segunda fuente",
                "url": "https://example.net/research",
                "domain": "example.net",
                "provider": "provider-b",
                "text": "Segunda evidencia documental para contrastar los datos disponibles.",
            },
        ],
        "search_results": [],
        "verification": {"distinct_providers": 2},
        "contrast_required": True,
    }
    return {
        "messages": [
            {
                "role": "system",
                "content": (
                    "Contexto de síntesis. Server-side web evidence for this request follows."
                    + json.dumps(evidence, ensure_ascii=False)
                ),
            },
            {"role": "user", "content": "Investiga y responde quién fue."},
        ],
    }


def test_web_evidence_is_never_emitted_as_extractive_answer() -> None:
    payload = evidence_payload()

    fallback = _extractive_web_evidence_fallback(payload)

    assert fallback is None


def test_compacted_retry_keeps_source_evidence_for_model_synthesis() -> None:
    payload = evidence_payload()

    compacted = _compact_web_synthesis_payload(payload)

    assert compacted is not None
    instruction = compacted["messages"][0]["content"]
    evidence_content = compacted["messages"][1]["content"]
    assert "Responde directamente a la última pregunta" in instruction
    assert "resume cada historia distinta" in instruction
    assert "Nunca respondas solo con el número de noticias" in instruction
    assert "resume cada una" in instruction
    assert "Excluye publirreportajes" in instruction
    assert "Fuente histórica" in evidence_content
    assert "example.org/history" in evidence_content
    assert "Contexto de síntesis" not in evidence_content
    assert "no debe convertirse en la respuesta final" in evidence_content
    assert compacted["messages"][2]["content"] == "Investiga y responde quién fue."
    assert compacted["max_tokens"] == 768
    assert compacted["stream"] is False


def test_inference_diagnostic_returns_structured_failure_instead_of_http_500(monkeypatch) -> None:
    async def fail_inference(_request_id: str):
        raise RuntimeError("provider chain unavailable")

    monkeypatch.setattr(main, "request_id_from_request", lambda _request: "diagnostic-test")
    monkeypatch.setattr(main, "run_inference_check", fail_inference)

    result = asyncio.run(main.inference_check(object()))

    assert result["status"] == "FAIL"
    assert result["request_id"] == "diagnostic-test"
    assert result["error_type"] == "RuntimeError"
    assert result["attempts"] == []



def test_news_search_query_drops_answer_instructions_before_provider_search() -> None:
    query = (
        "Busca las últimas noticias de Chile hoy. "
        "Resume cada historia distinta con evidencia suficiente y termina con NEWS_STREAM_PASS."
    )

    assert _normalize_web_search_query(query) == "últimas noticias de Chile hoy"
