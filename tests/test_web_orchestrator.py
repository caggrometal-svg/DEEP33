from __future__ import annotations

import asyncio

from backend.web_orchestrator import ServerWebOrchestrator


def test_server_orchestrator_search_fetch_then_normal_gateway() -> None:
    events = []

    async def search(query, **kwargs):
        events.append(("search", query))
        return {
            "ok": True,
            "results": [
                {"title": "A", "url": "https://a.example/source", "snippet": "A"},
                {"title": "A duplicate", "url": "https://a.example/other", "snippet": "A2"},
                {"title": "B", "url": "https://b.example/source", "snippet": "B"},
            ],
        }

    async def fetch(url, **kwargs):
        events.append(("fetch", url))
        return {
            "ok": True,
            "url": url,
            "final_url": url,
            "title": url,
            "text": "retrieved evidence",
        }

    async def gateway(payload, **kwargs):
        events.append(("gateway", payload))
        assert "tools" not in payload
        assert "tool_choice" not in payload
        assert any(
            m.get("role") == "system"
            and "DEEP33 WEB RETRIEVAL CONTEXT" in m.get("content", "")
            for m in payload["messages"]
        )
        return {
            "model": "test",
            "choices": [{"message": {"role": "assistant", "content": "WEB PASS"}}],
            "_deep33_gateway": {"provider": "kilo", "model": "test"},
        }

    data, sources = asyncio.run(
        ServerWebOrchestrator(search, fetch).run(
            [{"role": "user", "content": "investiga DEEP33"}],
            query="investiga DEEP33",
            model="test",
            request_id="r1",
            idempotency_key="i1",
            deadline=None,
            call_gateway=gateway,
        )
    )
    assert data["choices"][0]["message"]["content"] == "WEB PASS"
    assert len(sources) == 3
    assert [event[0] for event in events] == ["search", "fetch", "fetch", "gateway"]


def test_server_orchestrator_search_failure_degrades_to_normal_inference() -> None:
    async def search(query, **kwargs):
        raise RuntimeError("search unavailable")

    async def fetch(url, **kwargs):
        raise AssertionError("fetch must not run")

    async def gateway(payload, **kwargs):
        assert "WEB RETRIEVAL FAILED" in payload["messages"][-1]["content"]
        assert "tools" not in payload
        assert "tool_choice" not in payload
        return {
            "model": "test",
            "choices": [{"message": {"role": "assistant", "content": "DEGRADED PASS"}}],
        }

    data, sources = asyncio.run(
        ServerWebOrchestrator(search, fetch).run(
            [{"role": "user", "content": "busca DEEP33"}],
            query="busca DEEP33",
            model="test",
            request_id="r2",
            idempotency_key="i2",
            deadline=None,
            call_gateway=gateway,
        )
    )
    assert data["choices"][0]["message"]["content"] == "DEGRADED PASS"
    assert sources == []


def test_server_orchestrator_fetch_failure_keeps_search_sources() -> None:
    async def search(query, **kwargs):
        return {
            "ok": True,
            "results": [{"title": "A", "url": "https://a.example/source", "snippet": "A"}],
        }

    async def fetch(url, **kwargs):
        raise RuntimeError("fetch unavailable")

    async def gateway(payload, **kwargs):
        assert "https://a.example/source" in payload["messages"][-1]["content"]
        return {"model": "test", "choices": [{"message": {"role": "assistant", "content": "FETCH DEGRADED PASS"}}]}

    data, sources = asyncio.run(
        ServerWebOrchestrator(search, fetch).run(
            [{"role": "user", "content": "busca DEEP33"}],
            query="busca DEEP33",
            model="test",
            request_id="r3",
            idempotency_key="i3",
            deadline=None,
            call_gateway=gateway,
        )
    )
    assert data["choices"][0]["message"]["content"] == "FETCH DEGRADED PASS"
    assert len(sources) == 1
