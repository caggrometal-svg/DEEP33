from __future__ import annotations

import asyncio

import httpx

from tools.web_search import _parse_bing_rss, _plain_rss_snippet


def test_plain_rss_snippet_removes_html_tags_and_decodes_entities() -> None:
    snippet = (
        '<a href="https://news.google.com/rss/articles/example">Headline</a>'
        '&nbsp;&nbsp;<font color="#666">Publisher Name</font>'
    )

    result = _plain_rss_snippet(snippet)

    assert result == "Headline Publisher Name"
    assert "<a" not in result
    assert "href=" not in result


def test_rss_parser_returns_clean_description_text() -> None:
    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0"><channel><item>
      <title>Headline for a current report</title>
      <link>https://example.com/report</link>
      <description><![CDATA[<a href="https://example.com/report">Headline</a>&nbsp;<font>Publisher Name</font>]]></description>
    </item></channel></rss>"""

    result = asyncio.run(_parse_bing_rss(httpx.Response(200, content=xml), 5))

    assert len(result) == 1
    assert result[0]["snippet"] == "Headline Publisher Name"
    assert "<font" not in result[0]["snippet"]

def test_cl0q_response_normalizer_keeps_external_web_results():
    from tools.web_search import _cl0q_result_items

    results = _cl0q_result_items(
        {
            "results": [
                {
                    "url": "https://science.nasa.gov/moon/",
                    "title": "NASA Artemis lunar exploration",
                    "description": "Research and missions involving the Moon.",
                },
                {
                    "url": "https://www.esa.int/Science_Exploration/Space_Science",
                    "title": "ESA space science",
                    "snippet": "Science missions and current research.",
                },
            ]
        },
        5,
    )
    assert len(results) == 2
    assert results[0]["url"] == "https://science.nasa.gov/moon/"
    assert results[0]["snippet"] == "Research and missions involving the Moon."
    assert results[1]["url"].startswith("https://www.esa.int/")


def test_cl0q_response_normalizer_rejects_unknown_payload():
    import pytest
    from tools.web_search import WebSearchError, _cl0q_result_items

    with pytest.raises(WebSearchError, match="WEB_SEARCH_CL0Q_INVALID_RESPONSE"):
        _cl0q_result_items({"message": "not a results payload"}, 5)

def test_orchestrator_waits_for_two_successful_independent_providers():
    from backend.search.resilient import WebOrchestrator

    async def provider_one(query, timeout, limit):
        return [
            {
                "title": "NASA Artemis mission and lunar exploration",
                "url": "https://science.nasa.gov/artemis/",
                "snippet": "NASA Artemis mission lunar exploration update.",
            },
            {
                "title": "ESA Artemis lunar research mission",
                "url": "https://www.esa.int/Science_Exploration/Artemis",
                "snippet": "Artemis mission lunar science research.",
            },
            {
                "title": "Space.com Artemis mission guide",
                "url": "https://www.space.com/artemis-mission",
                "snippet": "A guide to the NASA Artemis lunar mission.",
            },
        ]

    async def provider_two(query, timeout, limit):
        await asyncio.sleep(0.04)
        return [
            {
                "title": "NASA Artemis mission update from independent index",
                "url": "https://spacenews.com/artemis-mission-update/",
                "snippet": "NASA Artemis mission update and lunar exploration.",
            }
        ]

    async def no_results(query, timeout, limit):
        return []

    async def exercise():
        orchestrator = WebOrchestrator(
            max_results=5,
            provider_total_seconds=0.2,
            global_budget_seconds=0.8,
            fallback_delay_seconds=0,
        )
        orchestrator.providers = {
            "bing": provider_one,
            "google": provider_two,
            "cl0q": no_results,
            "mojeek": no_results,
            "duckduckgo": no_results,
        }
        return await orchestrator.search("NASA Artemis mission", fresh=True)

    result = asyncio.run(exercise())
    assert result["ok"] is True
    assert result["verification"]["distinct_domains"] >= 3
    assert result["verification"]["distinct_providers"] >= 2
    assert {"bing", "google"}.issubset(set(result["providers"]))
def test_google_provider_returns_parsed_candidates_for_central_relevance_filter(monkeypatch):
    from tools import web_search

    class FakeClient:
        async def get(self, *args, **kwargs):
            return httpx.Response(
                200,
                text=(
                    '<html><body><a href="https://weather.example/Santiago-Chile">'
                    '<h3>Santiago Chile Weather Forecast</h3></a></body></html>'
                ),
                request=httpx.Request("GET", "https://www.google.com/search"),
            )

    async def fake_client():
        return FakeClient()

    monkeypatch.setattr(web_search, "_search_http_client", fake_client)
    results = asyncio.run(web_search._google_search("clima actual Santiago Chile", 1.0, 5))
    assert len(results) == 1
    assert results[0]["title"] == "Santiago Chile Weather Forecast"
    assert results[0]["url"] == "https://weather.example/Santiago-Chile"



def test_smartphone_recovery_query_uses_common_web_search_terms():
    from backend.search.resilient import WebOrchestrator

    query = "producto tecnológico smartphone actual especificaciones"
    assert WebOrchestrator._recovery_query(query) == (
        "smartphone specifications current models comparison review"
    )


def test_current_chile_politics_search_recovers_when_only_three_domains_are_found():
    from backend.search.resilient import WebOrchestrator

    initial_query = "política Chile hoy últimas noticias"
    recovered_queries = []

    def news_item(domain, title_suffix):
        return {
            "title": f"Últimas noticias de política en Chile: {title_suffix}",
            "url": f"https://{domain}/chile/politica",
            "snippet": (
                f"Chile política: noticias actuales del gobierno y Congreso. "
                f"Información sobre {title_suffix}."
            ),
        }

    async def provider(query, timeout, limit):
        recovered_queries.append(query)
        if "gobierno Congreso elecciones actualidad" in query:
            return [
                news_item("gobierno.example", "Gobierno y Congreso"),
                news_item("radio.example", "Actualidad política"),
                news_item("diario.example", "Noticias nacionales"),
            ]
        return [
            news_item("megapublisher.example", "Últimas noticias"),
            news_item("international.example", "Actualidad en Chile"),
            news_item("regional.example", "Política nacional"),
        ]

    async def empty(query, timeout, limit):
        return []

    async def exercise():
        orchestrator = WebOrchestrator(
            max_results=5,
            provider_total_seconds=0.2,
            global_budget_seconds=0.8,
            fallback_delay_seconds=0,
        )
        orchestrator.providers = {
            "bing": provider,
            "google": empty,
            "cl0q": empty,
            "mojeek": empty,
            "duckduckgo": empty,
        }
        return await orchestrator.search(initial_query, fresh=True)

    result = asyncio.run(exercise())
    assert result["ok"] is True
    assert any(
        query.startswith("Chile política gobierno Congreso elecciones actualidad noticias")
        for query in recovered_queries
    )
    assert result["verification"]["distinct_domains"] >= 5


def test_chile_government_site_recovery_uses_official_portal_terms():
    from backend.search.resilient import WebOrchestrator

    assert WebOrchestrator._recovery_query(
        "sitio gubernamental Chile información pública"
    ) == (
        "Chile official government public services information "
        "transparency open data ChileAtiende gob.cl"
    )


def test_primary_search_reserves_deadline_for_multisource_recovery():
    from backend.search.resilient import WebOrchestrator

    initial_query = "política Chile hoy últimas noticias"

    def item(domain, title):
        return {
            "title": title,
            "url": f"https://{domain}/chile/politica",
            "snippet": f"Política Chile noticias: {title}",
        }

    async def provider_for(name):
        async def provider(query, timeout, limit):
            if "BioBioChile Cooperativa Emol" not in query:
                if name == "bing":
                    return [item("solo.example", "Política Chile última noticia")]
                await asyncio.sleep(0.6)
                return []
            if name == "bing":
                return [
                    item("biobiochile.cl", "Política Chile noticias gobierno"),
                    item("cooperativa.cl", "Política Chile últimas noticias"),
                    item("emol.com", "Política Chile noticias de hoy"),
                ]
            if name == "duckduckgo":
                return [
                    item("latercera.com", "Política Chile últimas noticias del gobierno"),
                    item("24horas.cl", "Noticias política en Chile hoy"),
                ]
            return []

        return provider

    async def exercise():
        orchestrator = WebOrchestrator(
            max_results=5,
            provider_total_seconds=0.2,
            global_budget_seconds=0.4,
            fallback_delay_seconds=0,
            cache_ttl_seconds=30,
        )
        orchestrator.providers = {
            name: await provider_for(name)
            for name in ("bing", "google", "cl0q", "mojeek", "duckduckgo")
        }
        return await orchestrator.search(initial_query, fresh=True)

    result = asyncio.run(exercise())
    assert result["ok"] is True
    assert result["verification"]["distinct_providers"] >= 2
    assert result["verification"]["distinct_domains"] >= 3
    assert len(result["results"]) >= 3


def test_official_chile_portal_query_is_not_compressed_into_generic_topic_tokens():
    from backend.search.resilient import WebOrchestrator

    query = "sitio gubernamental Chile información pública"
    provider_query = WebOrchestrator._provider_query(query)
    assert "Chile" in provider_query
    assert "government" in provider_query
    assert "ChileAtiende" in provider_query
