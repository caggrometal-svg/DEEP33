from __future__ import annotations

import asyncio

from backend.search.engine import SearchEngine, rank_results
import backend.search.engine as search_engine


def test_search_rejects_unrelated_results_before_returning_them() -> None:
    results = [
        {
            "title": "Papa Johns Pizza Menu",
            "url": "https://papajohns.cl/menu",
            "snippet": "Order pizza online for delivery.",
        },
        {
            "title": "PizzaPizza — order pizza",
            "url": "https://pizzapizza.cl/",
            "snippet": "Fresh pizza and family meals.",
        },
        {
            "title": "asyncio — Asynchronous I/O",
            "url": "https://docs.python.org/3/library/asyncio.html",
            "snippet": "Python documentation for asynchronous I/O.",
        },
    ]

    ranked = rank_results("Python asyncio official documentation", results)

    assert len(ranked) == 1
    assert ranked[0]["url"] == "https://docs.python.org/3/library/asyncio.html"


def test_topic_aliases_preserve_world_health_organization_results() -> None:
    results = [
        {
            "title": "Dengue and severe dengue",
            "url": "https://www.who.int/news-room/fact-sheets/detail/dengue-and-severe-dengue",
            "snippet": "World Health Organization information about dengue.",
        },
        {
            "title": "Family pizza menu",
            "url": "https://papajohns.cl/",
            "snippet": "Pizza deals and delivery.",
        },
        {
            "title": "Dengue research and prevention",
            "url": "https://www.cnn.com/health/dengue",
            "snippet": "The World Health Organization describes dengue symptoms and prevention.",
        },
    ]

    ranked = rank_results("World Health Organization dengue fact sheet", results)

    assert len(ranked) == 2
    assert any(item["url"].startswith("https://www.who.int/") for item in ranked)
    assert any(item["url"].startswith("https://www.cnn.com/") for item in ranked)


def test_search_keeps_trying_providers_until_relevant_domains_are_diverse(monkeypatch) -> None:
    engine = SearchEngine(max_results=5, max_queries=1)
    monkeypatch.setattr(search_engine, "PARALLEL_PROVIDERS", True)

    async def fake_provider_search(
        provider: str,
        query: str,
        *,
        timeout_seconds: float,
        api_key: str,
        fast_mode: bool = False,
    ):
        if provider == "bing":
            return [
                {
                    "title": "NASA Artemis Mission",
                    "url": "https://www.nasa.gov/missions/artemis/",
                    "snippet": "NASA Artemis mission information.",
                },
                {
                    "title": "NASA Artemis Mission Updates",
                    "url": "https://www.nasa.gov/missions/artemis/updates/",
                    "snippet": "NASA Artemis mission updates.",
                },
            ], []
        return [
            {
                "title": "NASA Artemis mission explained",
                "url": "https://www.space.com/nasa-artemis-mission",
                "snippet": "NASA Artemis mission overview and background.",
            },
            {
                "title": "NASA Artemis mission coverage",
                "url": "https://www.bbc.com/news/science-environment-artemis",
                "snippet": "NASA Artemis mission news and details.",
            },
            ], []

    monkeypatch.setattr(engine, "_provider_search_with_retry", fake_provider_search)
    successful, errors, attempted = asyncio.run(
        engine._run_query(
            ["bing", "duckduckgo"],
            "NASA Artemis mission official",
            plan_depth="standard",
            timeout_seconds=2.0,
            api_key="",
        )
    )

    assert set(attempted) == {"bing", "duckduckgo"}
    assert {provider for provider, _items in successful} == {"bing", "duckduckgo"}
    assert not any("no_relevant_results" in error for error in errors)


def test_irrelevant_provider_results_do_not_cancel_relevant_fallback(monkeypatch) -> None:
    engine = SearchEngine(max_results=5, max_queries=1)
    monkeypatch.setattr(search_engine, "PARALLEL_PROVIDERS", True)

    async def fake_provider_search(
        provider: str,
        query: str,
        *,
        timeout_seconds: float,
        api_key: str,
        fast_mode: bool = False,
    ):
        if provider == "bing":
            return [
                {
                    "title": "Papa Johns Pizza",
                    "url": "https://papajohns.cl/",
                    "snippet": "Pizza delivery.",
                },
                {
                    "title": "PizzaPizza",
                    "url": "https://pizzapizza.cl/",
                    "snippet": "Pizza meals.",
                },
            ], []
        return [
            {
                "title": "asyncio — Asynchronous I/O",
                "url": "https://docs.python.org/3/library/asyncio.html",
                "snippet": "Python asyncio documentation.",
            },
            {
                "title": "Asyncio — Python library",
                "url": "https://realpython.com/async-io-python/",
                "snippet": "Python asyncio and asynchronous programming.",
            },
        ], []

    monkeypatch.setattr(engine, "_provider_search_with_retry", fake_provider_search)
    successful, errors, attempted = asyncio.run(
        engine._run_query(
            ["bing", "duckduckgo"],
            "Python asyncio official documentation",
            plan_depth="standard",
            timeout_seconds=2.0,
            api_key="",
        )
    )

    assert set(attempted) == {"bing", "duckduckgo"}
    assert {provider for provider, _items in successful} == {"duckduckgo"}
    assert "bing:no_relevant_results:2" in errors


def test_edge_search_filters_irrelevant_non_realtime_results_in_both_routes() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    for name in ("deep33-proxy", "deep33-tertiary"):
        source = (
            root / "supabase" / "functions" / name / "index.ts"
        ).read_text(encoding="utf-8")
        assert "function edgeFilterNonRealtimeResults(" in source
        assert "if (!realtime) return edgeFilterNonRealtimeResults(query, items);" in source


def test_search_prefers_readable_html_over_pdf_unless_pdf_is_requested() -> None:
    results = [
        {
            "title": "Artificial intelligence research PDF paper",
            "url": "https://research.example.edu/files/ai-research.pdf",
            "snippet": "Artificial intelligence research PDF paper and findings.",
        },
        {
            "title": "Artificial intelligence research program",
            "url": "https://research.example.org/artificial-intelligence-research",
            "snippet": "Artificial intelligence research program and findings.",
        },
    ]

    readable_first = rank_results("artificial intelligence research", results)
    assert readable_first[0]["url"] == "https://research.example.org/artificial-intelligence-research"

    pdf_requested = rank_results("artificial intelligence research PDF", results)
    assert pdf_requested[0]["url"] == "https://research.example.edu/files/ai-research.pdf"
