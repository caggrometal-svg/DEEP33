from __future__ import annotations

import asyncio

from backend.search.engine import SearchEngine, deduplicate, rank_results


def test_search_plan_expands_deep_queries():
    plan = SearchEngine(max_queries=3).plan("investiga DEEP33")
    assert plan.depth == "deep"
    assert len(plan.queries) == 3
    assert plan.queries[0] == "investiga DEEP33"


def test_rank_results_prefers_relevant_and_quality_sources():
    results = rank_results(
        "DEEP33 internet",
        [
            {"title": "Something else", "url": "https://example.net/", "snippet": "unrelated"},
            {"title": "DEEP33 internet", "url": "https://example.gov/", "snippet": "DEEP33 internet source"},
        ],
    )
    assert results[0]["url"] == "https://example.gov/"
    assert results[0]["score"] > results[1]["score"]


def test_deduplicate_normalizes_url_and_title():
    results = deduplicate(
        [
            {"title": "Same", "url": "https://example.com/a/"},
            {"title": "Same", "url": "https://example.com/a#fragment"},
            {"title": "Other", "url": "https://example.com/b"},
        ],
        5,
    )
    assert len(results) == 2
    assert results[0]["url"] == "https://example.com/a"


def test_engine_merges_planned_searches(monkeypatch):
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
    assert result["depth"] == "deep"
    assert len(result["queries"]) == 3
    assert len(result["results"]) == 2
    assert result["provider"] == "tavily"
