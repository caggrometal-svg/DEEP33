from __future__ import annotations

import asyncio

import pytest
from backend import main


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


def test_web_loop_rewrites_near_verbatim_web_output(monkeypatch: pytest.MonkeyPatch):
    calls = {"gateway": 0}

    async def fake_gateway(payload, **kwargs):
        calls["gateway"] += 1
        if calls["gateway"] == 1:
            return {
                "model": "test",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "La energía solar fotovoltaica convierte directamente la luz del sol en electricidad mediante semiconductores.",
                        }
                    }
                ],
            }
        return {
            "model": "test",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "DEEP33 resume el punto: los paneles usan materiales semiconductores para transformar la radiación solar en energía eléctrica.",
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
                    "snippet": "La energía solar fotovoltaica convierte directamente la luz del sol en electricidad mediante semiconductores.",
                }
            ],
        }

    async def fake_fetch(url, **kwargs):
        return {
            "ok": True,
            "url": url,
            "final_url": url,
            "title": "Solar",
            "text": "La energía solar fotovoltaica convierte directamente la luz del sol en electricidad mediante semiconductores.",
        }

    monkeypatch.setattr(main, "call_gateway", fake_gateway)
    monkeypatch.setattr(main, "search_web", fake_search)
    monkeypatch.setattr(main, "fetch_page", fake_fetch)

    result, _ = asyncio.run(
        main.run_web_tool_loop(
            [{"role": "system", "content": "web"}, {"role": "user", "content": "busca energía solar"}],
            model="test",
            request_id="r-originality",
            idempotency_key="i-originality",
            force_web=True,
            personality="NEUTRO",
        )
    )

    assert calls["gateway"] == 2
    assert result["choices"][0]["message"]["content"] == (
        "DEEP33 resume el punto: los paneles usan materiales semiconductores para transformar la radiación solar en energía eléctrica."
    )


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
