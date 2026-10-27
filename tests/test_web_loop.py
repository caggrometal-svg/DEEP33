from __future__ import annotations

import asyncio

import pytest
from backend import main
from backend.search.engine import is_realtime_query


def test_web_tool_loop_server_side_search_then_normal_generation(monkeypatch: pytest.MonkeyPatch):
    calls = {"gateway": 0, "search": 0, "fetch": 0}

    async def fake_gateway(payload, **kwargs):
        calls["gateway"] += 1
        assert "tools" not in payload
        assert "tool_choice" not in payload
        assert any(
            m.get("role") == "system" and "Server-side web evidence" in m.get("content", "")
            for m in payload["messages"]
        )
        assert any(
            m.get("role") == "system"
            and "No web tools or function-call interface are available" in m.get("content", "")
            for m in payload["messages"]
        )
        assert not any(
            m.get("role") == "system" and "Use them for current, external, changing" in m.get("content", "")
            for m in payload["messages"]
        )
        assert any(
            m.get("role") == "system"
            and "FINAL DEEP33 STYLE LOCK. ACTIVE_PERSONALITY=COMICO" in m.get("content", "")
            and "never copy, paste" in m.get("content", "")
            for m in payload["messages"]
        )
        return {
            "model": "test",
            "choices": [
                {"message": {"role": "assistant", "content": "Respuesta verificada."}}
            ],
        }

    async def fake_search(query, **kwargs):
        calls["search"] += 1
        assert "DEEP33" in query
        return {
            "ok": True,
            "query": query,
            "results": [
                {
                    "title": "Example",
                    "url": "https://example.com/",
                    "snippet": "Snippet",
                }
            ],
        }

    async def fake_fetch(url, **kwargs):
        calls["fetch"] += 1
        assert url == "https://example.com/"
        return {
            "ok": True,
            "url": url,
            "final_url": url,
            "title": "Example",
            "text": "REAL_ORIGINAL_PAGE_CONTENT " + ("Example article details, facts, and context. " * 4),
        }

    monkeypatch.setattr(main, "call_gateway", fake_gateway)
    monkeypatch.setattr(main, "search_web", fake_search)
    monkeypatch.setattr(main, "fetch_page", fake_fetch)

    result, sources = asyncio.run(
        main.run_web_tool_loop(
            [
                {"role": "system", "content": "web"},
                {"role": "user", "content": "busca en internet DEEP33"},
            ],
            model="test",
            request_id="r1",
            idempotency_key="i1",
            force_web=True,
            personality="COMICO",
        )
    )

    assert result["choices"][0]["message"]["content"] == "Respuesta verificada."
    assert sources[0]["url"] == "https://example.com/"
    assert calls == {"gateway": 1, "search": 1, "fetch": 1}

def test_source_metadata_never_leaks_into_final_text() -> None:
    final = main.sanitize_assistant_text(
        "Respuesta sintetizada.\n\nFuentes consultadas:\n1. [Example](https://example.com/)\n2. https://example.org/",
        [
            {"title": "Example", "url": "https://example.com/"},
            {"title": "Example 2", "url": "https://example.org/"},
        ],
    )
    assert final == "Respuesta sintetizada."
    assert "Fuentes consultadas" not in final
    assert "example.com" not in final
    assert "example.org" not in final


def test_sanitize_assistant_text_never_exposes_retrieved_sources() -> None:
    from backend.main import sanitize_assistant_text

    source = {"title": "Example", "url": "https://example.com/source"}
    text = (
        "La conclusión de DEEP33 está aquí.\n\n"
        "Fuentes consultadas:\n"
        "1. [Example](https://example.com/source)"
    )
    assert sanitize_assistant_text(text, [source]) == "La conclusión de DEEP33 está aquí."

def test_sanitize_assistant_text_removes_all_common_source_forms() -> None:
    from backend.main import sanitize_assistant_text

    text = (
        "Síntesis propia de DEEP33. "
        "[Ver estudio](https://example.com/source) "
        "https://example.org/raw "
        "(Fuente: Example Research) "
        "[Source: Example Research]\n\n"
        "## Referencias\n"
        "- Example Research\n"
        "- Another source"
    )
    assert sanitize_assistant_text(text, []) == "Síntesis propia de DEEP33."


def test_sanitize_assistant_text_removes_provider_citation_markup() -> None:
    from backend.main import sanitize_assistant_text

    text = (
        "DEEP33 entrega la conclusión. "
        "citeturn1search2 "
        "[^1] [2] "
        "<a href=\"https://example.com\">Example</a>\n"
        "Fuente 1: https://example.com/source\n"
        "Retrieved from https://example.org/source"
    )
    assert sanitize_assistant_text(text, []) == "DEEP33 entrega la conclusión."


def test_sanitize_generation_output_preserves_safe_source_metadata() -> None:
    from backend.main import sanitize_generation_output

    output = {
        "result": {
            "role": "assistant",
            "text": "Respuesta. citesource1",
        },
        "response": {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "Respuesta. [Fuente: https://example.com]",
                    }
                }
            ]
        },
        "sources": [{"title": "Example", "url": "https://example.com"}],
    }
    sanitized = sanitize_generation_output(output)
    assert sanitized["sources"] == [{
        "title": "Example",
        "url": "https://example.com",
        "snippet": "",
        "provider": "unknown",
    }]
    assert sanitized["web_navigation"] is False
    assert "cite" not in sanitized["result"]["text"]
    assert "example.com" not in sanitized["response"]["choices"][0]["message"]["content"]


def test_web_loop_uses_single_synthesis_pass(monkeypatch: pytest.MonkeyPatch):
    calls = {"gateway": 0}

    async def fake_gateway(payload, **kwargs):
        calls["gateway"] += 1
        return {
            "model": "test",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "Síntesis original basada en la evidencia.",
                    }
                }
            ],
        }

    async def fake_search(query, **kwargs):
        return {
            "ok": True,
            "results": [
                {
                    "title": "Solar",
                    "url": "https://example.com/solar",
                    "snippet": "Fuente de evidencia.",
                }
            ],
        }

    async def fake_fetch(url, **kwargs):
        return {
            "ok": True,
            "url": url,
            "final_url": url,
            "title": "Solar",
            "text": "Texto de evidencia.",
        }

    monkeypatch.setattr(main, "call_gateway", fake_gateway)
    monkeypatch.setattr(main, "search_web", fake_search)
    monkeypatch.setattr(main, "fetch_page", fake_fetch)

    result, _ = asyncio.run(
        main.run_web_tool_loop(
            [{"role": "user", "content": "busca energía solar"}],
            model="test",
            request_id="r-single",
            idempotency_key="i-single",
            force_web=True,
            personality="NEUTRO",
        )
    )

    assert calls["gateway"] == 1
    assert result["choices"][0]["message"]["content"] == "Síntesis original basada en la evidencia."


def test_web_loop_final_style_lock_preserves_selected_personality(monkeypatch: pytest.MonkeyPatch):
    async def fake_gateway(payload, **kwargs):
        system_text = "\n".join(
            str(m.get("content", "")) for m in payload["messages"] if m.get("role") == "system"
        )
        assert "FINAL DEEP33 STYLE LOCK" in system_text
        assert "ACTIVE_PERSONALITY=AGRESIVO" in system_text
        assert "never copy" in system_text.lower()
        return {
            "model": "test",
            "choices": [
                {"message": {"role": "assistant", "content": "Respuesta sintetizada."}}
            ],
        }

    async def fake_search(query, **kwargs):
        return {
            "ok": True,
            "results": [
                {
                    "title": "Example",
                    "url": "https://example.com/",
                    "snippet": "Raw source wording.",
                }
            ],
        }

    async def fake_fetch(url, **kwargs):
        return {
            "ok": True,
            "url": url,
            "final_url": url,
            "title": "Example",
            "text": "Long source paragraph that must remain evidence only. " * 4,
        }

    monkeypatch.setattr(main, "call_gateway", fake_gateway)
    monkeypatch.setattr(main, "search_web", fake_search)
    monkeypatch.setattr(main, "fetch_page", fake_fetch)

    result, sources = asyncio.run(
        main.run_web_tool_loop(
            [
                {"role": "system", "content": "web"},
                {"role": "user", "content": "busca en internet DEEP33"},
            ],
            model="test",
            request_id="r-personality",
            idempotency_key="i-personality",
            force_web=True,
            personality="AGRESIVO",
        )
    )

    assert result["choices"][0]["message"]["content"] == "Respuesta sintetizada."
    assert sources


def test_stream_gateway_forwards_provider_chunks_before_stream_completion(monkeypatch):
    observed = []

    async def fake_stream(payload, **kwargs):
        observed.append("provider-start")
        yield b'data: {"choices":[{"delta":{"content":"Hola"}}]}\n\n'
        observed.append("provider-before-second")
        yield b'data: {"choices":[{"delta":{"content":" mundo"}}]}\n\n'
        yield b"data: [DONE]\n\n"
        observed.append("provider-finished")

    class NoMemory:
        enabled = False

    monkeypatch.setattr(main.gateway, "stream", fake_stream)
    monkeypatch.setattr(main, "memory", NoMemory())

    async def exercise():
        generator = main.stream_gateway(
            {"messages": [{"role": "user", "content": "hola"}], "model": "test"},
            "session",
            "NEUTRO",
            request_id="stream-regression",
            idempotency_key="stream-regression",
            request_hash="stream-regression",
            lease_token="lease",
        )
        first = await generator.__anext__()
        assert b'"content":"Hola"' in first
        assert observed == ["provider-start"]

        second = await generator.__anext__()
        assert b'"content":" mundo"' in second
        assert observed == ["provider-start", "provider-before-second"]

        third = await generator.__anext__()
        assert third == b"data: [DONE]\n\n"
        with pytest.raises(StopAsyncIteration):
            await generator.__anext__()
        assert observed[-1] == "provider-finished"

    asyncio.run(exercise())


def test_stream_gateway_suppresses_split_internal_tool_markup(monkeypatch):
    async def fake_stream(payload, **kwargs):
        yield b'data: {"choices":[{"delta":{"content":"<dots_func"}}]}\n\n'
        yield b'data: {"choices":[{"delta":{"content":"tion_call><invoke name=\\"web_search\\">"}}]}\n\n'
        yield b'data: {"choices":[{"delta":{"content":"</invoke></dots_function_call>"}}]}\n\n'
        yield b"data: [DONE]\n\n"

    class NoMemory:
        enabled = False

    monkeypatch.setattr(main.gateway, "stream", fake_stream)
    monkeypatch.setattr(main, "memory", NoMemory())

    async def exercise():
        generator = main.stream_gateway(
            {"messages": [{"role": "user", "content": "busca en internet"}], "model": "test"},
            "session",
            "NEUTRO",
            request_id="internal-tool-markup",
            idempotency_key="internal-tool-markup",
            request_hash="internal-tool-markup",
            lease_token="lease",
        )
        output = bytearray()
        async for part in generator:
            output.extend(part)
        assert b"dots_function_call" not in output.lower()
        assert b"<invoke" not in output.lower()
        assert b"AI_GATEWAY_INVALID_RESPONSE" in output

    asyncio.run(exercise())


def test_sanitize_assistant_text_drops_internal_tool_markup():
    text = 'Respuesta parcial. <dots_function_call><invoke name="web_search">'
    assert main.sanitize_assistant_text(text) == "Respuesta parcial."
    assert main.sanitize_assistant_text("<dots_function_call><invoke name=") == ""


def test_local_current_situation_forces_realtime_web_lookup():
    query = "¿Cuál es la situación actual de la comuna de Las Condes en Chile?"
    assert main.should_force_web([{"role": "user", "content": query}]) is True
    assert is_realtime_query(query)


def test_web_routing_skips_stable_chat_and_uses_fresh_external_signals():
    stable_messages = [{"role": "user", "content": "2x2?"}]
    current_messages = [{"role": "user", "content": "¿Cuál es el precio actual del dólar?"}]
    explicit_messages = [{"role": "user", "content": "busca en internet DEEP33"}]

    assert main.should_force_web(stable_messages) is False
    assert main.should_force_web(current_messages) is True
    assert main.should_force_web(explicit_messages) is True


def test_web_routing_covers_external_web_without_topic_specific_keywords():
    external_queries = (
        "¿Quién es el CEO de NVIDIA?",
        "¿Cuál es la versión estable de Python?",
        "¿Cuál es el horario del Museo del Prado?",
        "¿Qué dice la documentación de FastAPI sobre lifespan?",
        "¿Cuál es la normativa de equipaje de LATAM?",
    )
    for query in external_queries:
        assert main.should_force_web([{"role": "user", "content": query}]) is True, query


def test_web_routing_does_not_browse_for_stable_explanations_by_default():
    stable_queries = (
        "¿Qué es la fotosíntesis?",
        "Explica cómo funciona la recursión",
        "¿Por qué el cielo se ve azul?",
    )
    for query in stable_queries:
        assert main.should_force_web([{"role": "user", "content": query}]) is False, query


def test_runtime_clock_is_authoritative():
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo

    now_utc = datetime.now(timezone.utc)
    local = now_utc.astimezone(ZoneInfo(main.DEEP33_RUNTIME_TIMEZONE))
    context = main.runtime_clock_context()

    assert "RELOJ DE EJECUCIÓN DE DEEP33 — DATO AUTORITATIVO." in context
    assert f"Fecha ISO local: {local.date().isoformat()}." in context
    assert f"UTC: {now_utc.date().isoformat()}" in context


def test_date_questions_force_fresh_web_lookup():
    assert main.should_force_web([{"role": "user", "content": "¿Qué fecha es?"}]) is True
    assert main.should_force_web([{"role": "user", "content": "¿Qué día es hoy?"}]) is True
    assert main.should_force_web([{"role": "user", "content": "¿Qué hora es?"}]) is True


def test_required_web_search_failure_returns_one_explicit_answer_without_stale_model(
    monkeypatch: pytest.MonkeyPatch,
):
    async def failing_search(*_args, **_kwargs):
        raise RuntimeError("network down")

    async def no_weather(*_args, **_kwargs):
        return None

    async def forbidden_gateway(*_args, **_kwargs):
        raise AssertionError("Must not answer a current question from model memory")

    monkeypatch.setattr(main, "search_web", failing_search)
    monkeypatch.setattr(main, "resolve_gps_weather", no_weather)
    monkeypatch.setattr(main, "call_gateway", forbidden_gateway)

    data, sources = asyncio.run(
        main.run_web_tool_loop(
            [{"role": "user", "content": "¿Qué noticias relevantes se publicaron hoy en Chile?"}],
            model="test-model",
            request_id="web-failure-test",
            idempotency_key="web-failure-test",
            force_web=True,
            personality="NEUTRO",
        )
    )

    assert sources == []
    answer = data["choices"][0]["message"]["content"]
    assert "No pude verificar información actual" in answer
    assert "No voy a inventar datos ni enlaces" in answer


def test_prepare_web_evidence_marks_search_failure_without_http_503(
    monkeypatch: pytest.MonkeyPatch,
):
    async def failing_search(*_args, **_kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(main, "search_web", failing_search)

    working, sources, evidence, results = asyncio.run(
        main.prepare_web_evidence(
            [{"role": "user", "content": "¿Qué fecha es hoy?"}],
            request_id="web-failure-test",
        )
    )

    assert working is None
    assert sources == []
    assert evidence == []
    assert results == []


def test_connectivity_question_uses_live_backend_probe_not_search(monkeypatch: pytest.MonkeyPatch):
    assert main.is_connectivity_question("¿Tienes acceso a Internet?")
    assert main.is_connectivity_question("Do you have internet access?")
    assert not main.is_connectivity_question("Busca una noticia actual en Internet")

    async def fake_probe():
        return {
            "internet_available": True,
            "dns_ok": True,
            "https_ok": True,
            "latency_ms": 123.4,
            "timestamp": "2026-10-10T20:00:00Z",
            "error": None,
        }

    monkeypatch.setattr(main, "network_probe", fake_probe)
    answer = asyncio.run(main._connectivity_status_answer())

    assert "DNS y HTTPS respondieron" in answer
    assert "123.4 ms" in answer
    assert "no garantiza que todos los motores de búsqueda estén disponibles" in answer


def test_search_failure_preserves_runtime_clock_answer():
    answer = main._web_search_unavailable_answer("¿Qué hora y fecha es en Chile?")
    assert "reloj de ejecución del backend" in answer
    assert main.DEEP33_RUNTIME_TIMEZONE in answer
    assert "No pude verificar información actual" not in answer


def test_web_grounding_contract_forbids_model_memory_gap_filling() -> None:
    prompt = main.WEB_EVIDENCE_SYNTHESIS_PROMPT
    assert "GROUNDING CONTRACT" in prompt
    assert "every externally verifiable factual claim" in prompt
    assert "Never fill an evidence gap from model memory" in prompt
    assert "Fetched-page text outranks search snippets" in prompt
    assert "conflict" in prompt.lower()

def test_verified_source_citations_include_only_public_https_domains() -> None:
    sources = [
        {"title": "Python release notes", "url": "https://www.python.org/downloads/release/python-3151/"},
        {"title": "Python package index", "url": "https://pypi.org/project/python/"},
        {"title": "duplicate same host", "url": "https://python.org/downloads/release/python-3151/"},
        {"title": "insecure source", "url": "http://evil.example/info"},
        {"title": "private address", "url": "https://127.0.0.1/admin"},
        {"title": "search redirect", "url": "https://news.google.com/rss/articles/xyz"},
    ]

    safe = main.safe_web_sources(sources)
    rendered = main.format_source_citations(sources)

    assert [item["url"] for item in safe] == [
        "https://www.python.org/downloads/release/python-3151/",
        "https://pypi.org/project/python/",
    ]
    assert "[Python release notes](https://www.python.org/downloads/release/python-3151/)" in rendered
    assert "[Python package index](https://pypi.org/project/python/)" in rendered
    assert "evil.example" not in rendered
    assert "127.0.0.1" not in rendered
    assert "news.google.com" not in rendered


def test_json_answer_attaches_verified_sources_after_strict_sanitization() -> None:
    output = {
        "result": {
            "role": "assistant",
            "text": "La versión fue confirmada. [enlace falso](https://evil.example/).",
        },
        "response": {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "La versión fue confirmada. https://evil.example/",
                }
            }]
        },
        "sources": [{
            "title": "Python official release",
            "url": "https://www.python.org/downloads/release/python-3151/",
        }],
        "web_navigation": True,
    }

    actual = main.attach_verified_source_links(output)

    assert "https://www.python.org/downloads/release/python-3151/" in actual["result"]["text"]
    assert "https://www.python.org/downloads/release/python-3151/" in actual["response"]["choices"][0]["message"]["content"]
    assert "evil.example" not in actual["result"]["text"]
    assert "evil.example" not in actual["response"]["choices"][0]["message"]["content"]
    assert actual["web_navigation"] is True


def test_verified_sources_are_not_emitted_as_links_without_safe_urls() -> None:
    assert main.format_source_citations([
        {"title": "unsafe", "url": "http://example.com/"}
    ]) == ""



def test_fetched_page_relevance_rejects_irrelevant_redirect_content():
    source = {
        "title": "NVIDIA leadership",
        "url": "https://www.nvidia.com/en-us/about-nvidia/",
        "snippet": "NVIDIA leadership and executive team",
    }
    assert main._is_relevant_fetched_web_source(
        "¿quién dirige actualmente NVIDIA como CEO?",
        source,
        {
            "title": "NVIDIA leadership",
            "url": "https://www.nvidia.com/en-us/about-nvidia/",
            "text": "NVIDIA leadership and executive team. Jensen Huang is founder, president and CEO. " * 6,
        },
    ) is True

    assert main._is_relevant_fetched_web_source(
        "¿quién dirige actualmente NVIDIA como CEO?",
        source,
        {
            "title": "Sign in",
            "url": "https://example.com/login",
            "text": "Sign in to your account. Manage preferences and recover your password. " * 8,
        },
    ) is False
