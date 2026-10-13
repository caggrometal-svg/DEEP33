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


def test_realtime_smartphone_query_matches_english_specs_evidence_without_weakening_relevance() -> None:
    query = "producto tecnológico smartphone actual especificaciones"
    ranked = rank_results(
        query,
        [
            {
                "title": "Best smartphones: current models and specifications",
                "url": "https://www.techradar.com/phones/best-phones",
                "snippet": (
                    "Compare smartphone specifications, prices, camera and battery "
                    "for current mobile technology models."
                ),
            },
            {
                "title": "Top laptops and computer deals",
                "url": "https://www.example.com/laptop-deals",
                "snippet": "Current prices and technical specifications for laptop computers.",
            },
        ],
        realtime=True,
    )
    assert len(ranked) == 1
    assert ranked[0]["url"] == "https://www.techradar.com/phones/best-phones"


def test_realtime_chile_politics_matches_accentless_and_english_news_terms() -> None:
    query = "política Chile hoy últimas noticias"
    ranked = rank_results(
        query,
        [
            {
                "title": "Chile Politics: Latest Government and Congress News",
                "url": "https://news.example.org/chile-politica",
                "snippet": "Political updates about Chile's government and Congress.",
            },
            {
                "title": "Política chilena hoy: Gobierno y Congreso",
                "url": "https://noticias.example.cl/politica/chile",
                "snippet": "Últimas noticias de política en Chile.",
            },
            {
                "title": "Today's weather forecast in Chile",
                "url": "https://weather.example.net/chile",
                "snippet": "Weather and temperature forecast for Chile today.",
            },
        ],
        realtime=True,
    )
    assert len(ranked) == 2
    assert {item["url"] for item in ranked} == {
        "https://news.example.org/chile-politica",
        "https://noticias.example.cl/politica/chile",
    }


def test_realtime_santiago_weather_matches_weather_synonyms_but_not_city_news() -> None:
    query = "clima actual Santiago Chile"
    ranked = rank_results(
        query,
        [
            {
                "title": "Santiago Chile Weather Forecast",
                "url": "https://weather.example.net/Santiago-Chile",
                "snippet": "Current temperatures, wind and rainfall in Santiago.",
            },
            {
                "title": "Temperaturas actuales en Santiago",
                "url": "https://clima.example.cl/temperaturas/Santiago",
                "snippet": "Pronóstico meteorológico, humedad y condiciones actuales.",
            },
            {
                "title": "Santiago politics and government updates",
                "url": "https://news.example.org/Santiago-politics",
                "snippet": "Latest government and Congress news from Santiago.",
            },
        ],
        realtime=True,
    )
    assert {item["url"] for item in ranked} == {
        "https://weather.example.net/Santiago-Chile",
        "https://clima.example.cl/temperaturas/Santiago",
    }


def test_chile_current_politics_news_drops_foreign_articles_without_chile_context() -> None:
    query = "política Chile hoy últimas noticias"
    ranked = rank_results(
        query,
        [
            {
                "title": "Chile Political News: Latest Government Updates",
                "url": "https://news.example.org/chile-politics",
                "snippet": "Latest politics and government headlines in Chile today.",
            },
            {
                "title": "Política chilena hoy: Gobierno y Congreso",
                "url": "https://noticias.example.cl/politica/chile",
                "snippet": "Últimas noticias de política en Chile.",
            },
            {
                "title": "Mexico political news today",
                "url": "https://www.telediario.mx/politica",
                "snippet": "Latest government and election news in Mexico.",
            },
            {
                "title": "Politics of Chile - Encyclopedia",
                "url": "https://www.britannica.com/place/Chile/Politics",
                "snippet": "Historical overview of Chilean political institutions.",
            },
        ],
        realtime=True,
    )
    assert {item["url"] for item in ranked} == {
        "https://news.example.org/chile-politics",
        "https://noticias.example.cl/politica/chile",
    }


def test_chile_government_portal_query_ranks_local_public_sources_not_foreign_publishers() -> None:
    query = "sitio gubernamental Chile información pública"
    ranked = rank_results(
        query,
        [
            {
                "title": "Gobierno de Chile - Servicios públicos",
                "url": "https://www.gob.cl/",
                "snippet": "Información de servicios públicos oficiales del Gobierno de Chile.",
            },
            {
                "title": "ChileAtiende - trámites y beneficios del Gobierno de Chile",
                "url": "https://www.chileatiende.gob.cl/",
                "snippet": "Información sobre servicios públicos en Chile.",
            },
            {
                "title": "Datos Abiertos del Gobierno de Chile",
                "url": "https://www.datos.gob.cl/",
                "snippet": "Catálogo de información pública y datos abiertos oficiales.",
            },
            {
                "title": "Noticias de gobierno y servicios públicos",
                "url": "https://cuscatlanhoy.com/",
                "snippet": "Información pública y servicios del gobierno en El Salvador.",
            },
        ],
    )
    assert {item["url"] for item in ranked} == {
        "https://www.gob.cl/",
        "https://www.chileatiende.gob.cl/",
        "https://www.datos.gob.cl/",
    }


def test_generic_chile_named_locality_results_are_not_rejected_by_government_portal_guard() -> None:
    results = rank_results(
        "clima Las Condes Chile",
        [
            {
                "title": "Pronóstico del tiempo en Las Condes",
                "url": "https://weather.example/las-condes-live",
                "snippet": "Temperaturas actuales, lluvia y viento en Las Condes.",
            }
        ],
        realtime=True,
    )
    assert [item["url"] for item in results] == ["https://weather.example/las-condes-live"]
