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
            "text": "Example content",
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


def test_sanitize_generation_output_clears_source_metadata() -> None:
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
    assert sanitized["sources"] == []
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
            "text": "Long source paragraph that must remain evidence only.",
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
            [{"role": "user", "content": "¿Qué fecha es hoy?"}],
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
