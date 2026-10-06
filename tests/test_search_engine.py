from __future__ import annotations

import asyncio

from backend.search.engine import SearchEngine, deduplicate, rank_results


def test_search_plan_strips_conversational_web_instructions():
    plan = SearchEngine(max_queries=1).plan(
        "Busca en internet la fecha actual en Chile y responde solo: OK"
    )
    assert plan.queries == ["la fecha actual en Chile"]


def test_search_plan_expands_deep_queries():
    plan = SearchEngine(max_queries=3).plan("investiga DEEP33")
    assert plan.depth == "deep"
    assert len(plan.queries) == 3
    assert plan.queries[0] == "investiga DEEP33"


def test_rank_results_prefers_relevant_and_corroborated_sources():
    results = rank_results(
        "DEEP33 internet",
        [
            {
                "title": "Something unrelated",
                "url": "https://example.net/",
                "snippet": "different topic",
            },
            {
                "title": "DEEP33 internet report",
                "url": "https://source-a.org/report",
                "snippet": "DEEP33 internet source evidence",
            },
            {
                "title": "DEEP33 internet findings",
                "url": "https://source-b.org/findings",
                "snippet": "DEEP33 internet source evidence",
            },
        ],
    )
    assert results[0]["corroborated"] is True
    assert results[0]["corroboration_count"] >= 1
    assert results[0]["score"] > results[2]["score"]


def test_deduplicate_normalizes_url_and_guarantees_domain_diversity():
    results = deduplicate(
        [
            {"title": "A1", "url": "https://example.com/a/"},
            {"title": "A2", "url": "https://example.com/b"},
            {"title": "B1", "url": "https://other.example/a"},
            {"title": "Same", "url": "https://other.example/a#fragment"},
        ],
        3,
    )
    assert len(results) == 3
    assert results[0]["url"] == "https://example.com/a"
    assert {item["source_domain"] if "source_domain" in item else item["url"].split("/")[2] for item in results} >= {
        "example.com",
        "other.example",
    }


def test_engine_merges_planned_searches_without_provider_coupling(monkeypatch):
    monkeypatch.setenv("WEB_SEARCH_BING_ENABLED", "false")

    async def fake_tavily(query, api_key, timeout_seconds, max_results):
        return [
            {
                "title": f"Result {query}",
                "url": "https://example.com/a" if "fuente" not in query else "https://example.com/b",
                "snippet": query,
            }
        ]

    monkeypatch.setattr("tools.web_search._tavily_search", fake_tavily)
    result = asyncio.run(
        SearchEngine(max_results=5).search(
            "investiga DEEP33",
            provider="tavily",
            api_key="test-key",
            timeout_seconds=8,
            fallback_ddg=False,
        )
    )
    assert result["ok"] is True
    assert result["engine"] == "DEEP33 Search Engine"
    assert result["engine_version"] == "1.1.0"
    assert result["provider_independent"] is True
    assert result["depth"] == "deep"
    assert len(result["queries"]) == 3
    assert len(result["results"]) == 2
    assert result["provider"] == "tavily"


def test_deep_search_uses_multiple_independent_providers(monkeypatch):
    monkeypatch.setenv("WEB_SEARCH_BING_ENABLED", "true")

    async def fake_tavily(query, api_key, timeout_seconds, max_results):
        return [{
            "title": "Shared finding",
            "url": "https://alpha.org/finding",
            "snippet": "DEEP33 internet finding evidence",
        }]

    async def fake_bing(query, timeout_seconds, max_results):
        return [{
            "title": "Shared finding",
            "url": "https://beta.org/finding",
            "snippet": "DEEP33 internet finding evidence",
        }]

    async def fake_ddg(query, timeout_seconds, max_results):
        return [{
            "title": "Different result",
            "url": "https://gamma.net/other",
            "snippet": "unrelated",
        }]

    monkeypatch.setattr("tools.web_search._tavily_search", fake_tavily)
    monkeypatch.setattr("tools.web_search._bing_search", fake_bing)
    monkeypatch.setattr("tools.web_search._duckduckgo_search", fake_ddg)

    result = asyncio.run(
        SearchEngine(max_results=5).search(
            "investiga DEEP33",
            provider="auto",
            api_key="test-key",
            timeout_seconds=8,
            fallback_ddg=True,
        )
    )
    assert result["ok"] is True
    assert set(result["providers"]) == {"tavily", "bing", "duckduckgo"}
    assert result["verification"]["distinct_providers"] == 3
    assert result["verification"]["distinct_domains"] >= 3
    assert result["verification"]["corroborated_results"] >= 1


def test_standard_search_falls_through_on_weak_primary_results(monkeypatch):
    monkeypatch.setenv("WEB_SEARCH_BING_ENABLED", "true")

    async def fake_bing(query, timeout_seconds, max_results):
        return [{"title": "Weak", "url": "https://a.org/1", "snippet": query}]

    async def fake_ddg(query, timeout_seconds, max_results):
        return [
            {"title": "Strong 1", "url": "https://b.org/1", "snippet": query},
            {"title": "Strong 2", "url": "https://c.org/2", "snippet": query},
            {"title": "Strong 3", "url": "https://d.org/3", "snippet": query},
        ]

    monkeypatch.setattr("tools.web_search._bing_search", fake_bing)
    monkeypatch.setattr("tools.web_search._duckduckgo_search", fake_ddg)

    result = asyncio.run(
        SearchEngine(max_results=5).search(
            "DEEP33 internet",
            provider="bing",
            api_key="",
            timeout_seconds=8,
            fallback_ddg=True,
        )
    )
    assert set(result["providers"]) == {"bing", "duckduckgo"}
    assert len(result["results"]) == 4


def test_standard_search_parallel_provider_short_circuits(monkeypatch):
    monkeypatch.setenv("WEB_SEARCH_BING_ENABLED", "true")
    async def fake_bing(query, timeout_seconds, max_results):
        return [
            {"title": "Enough 1", "url": "https://bing.example/1", "snippet": query},
            {"title": "Enough 2", "url": "https://bing.example/2", "snippet": query},
            {"title": "Enough 3", "url": "https://bing.example/3", "snippet": query},
        ]

    async def fail_ddg(query, timeout_seconds, max_results):
        raise AssertionError("DDG should be cancelled once sufficient results arrive")

    monkeypatch.setattr("tools.web_search._bing_search", fake_bing)
    monkeypatch.setattr("tools.web_search._duckduckgo_search", fail_ddg)

    # The search module defaults to parallel provider acquisition; a sufficient
    # primary result must not force a sequential fallback request.
    result = asyncio.run(
        SearchEngine(max_results=5).search(
            "DEEP33",
            provider="bing",
            api_key="",
            timeout_seconds=8,
            fallback_ddg=True,
        )
    )
    assert result["ok"] is True


def test_fast_mode_uses_one_query_and_cancels_slower_provider(monkeypatch):
    monkeypatch.setenv("WEB_SEARCH_BING_ENABLED", "true")
    cancelled = {"value": False}

    async def fast_tavily(query, api_key, timeout_seconds, max_results):
        return [
            {"title": "One", "url": "https://fast.example/1", "snippet": query},
            {"title": "Two", "url": "https://fast.example/2", "snippet": query},
            {"title": "Three", "url": "https://fast.example/3", "snippet": query},
        ]

    async def slow_bing(query, timeout_seconds, max_results):
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            cancelled["value"] = True
            raise

    monkeypatch.setattr("tools.web_search._tavily_search", fast_tavily)
    monkeypatch.setattr("tools.web_search._bing_search", slow_bing)

    result = asyncio.run(
        SearchEngine(max_results=5, max_queries=3).search(
            "investiga DEEP33",
            provider="auto",
            api_key="test-key",
            timeout_seconds=8,
            fallback_ddg=False,
            fast_mode=True,
        )
    )

    assert result["ok"] is True
    assert len(result["queries"]) == 1
    assert result["results"]
    assert cancelled["value"] is True


def test_controversial_queries_expand_against_official_and_independent_versions():
    plan = SearchEngine(max_queries=3).plan("¿La versión oficial contradice la evidencia independiente?")
    assert plan.depth == "deep"
    assert any("versión oficial" in q.lower() for q in plan.queries[1:])
    assert any("evidencia independiente" in q.lower() for q in plan.queries[1:])


def test_search_web_fresh_mode_bypasses_cache(monkeypatch):
    import tools.web_search as web_search_module

    calls = {"count": 0}

    class FakeEngine:
        def __init__(self, *, max_results, max_queries):
            self.max_results = max_results
            self.max_queries = max_queries

        async def search(self, *args, **kwargs):
            calls["count"] += 1
            return {
                "ok": True,
                "engine": "DEEP33 Search Engine",
                "engine_version": "1.1.0",
                "provider_independent": True,
                "results": [{
                    "title": "Live result",
                    "url": f"https://example.com/live-{calls['count']}",
                    "snippet": "fresh",
                }],
            }

    monkeypatch.setattr("backend.search.engine.SearchEngine", FakeEngine)
    web_search_module._SEARCH_CACHE.clear()

    asyncio.run(web_search_module.search_web("DEEP33 ahora", provider="auto", fresh=True))
    asyncio.run(web_search_module.search_web("DEEP33 ahora", provider="auto", fresh=True))

    assert calls["count"] == 2
