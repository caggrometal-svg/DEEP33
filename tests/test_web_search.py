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
        "ChileAtiende datos.gob.cl cplt.cl gob.cl Chile "
        "public information government transparency servicios públicos"
    )




def test_google_news_recovery_keeps_realtime_marker(monkeypatch):
    from backend.search.resilient import WebOrchestrator
    import backend.search.resilient as module

    observed = []

    async def fake_google_news(query, timeout, limit):
        observed.append(query)
        return []

    monkeypatch.setattr(module, "_google_news_search", fake_google_news)

    orchestrator = WebOrchestrator(max_results=5)
    asyncio.run(
        orchestrator._google_news(
            "situación actual de la comuna de Las Condes en Chile",
            1.0,
            5,
        )
    )

    assert observed
    assert module._looks_realtime_news(observed[0])
    assert "condes" in observed[0].casefold()


def test_chile_public_information_fans_out_distinct_official_domains():
    from backend.search.resilient import WebOrchestrator

    calls = []

    def item(domain, title, snippet):
        return {
            "title": title,
            "url": f"https://{domain}/",
            "snippet": snippet,
            "source_domain": domain,
        }

    async def bing_provider(query, _timeout, _limit):
        calls.append(query)
        if "site:chileatiende.gob.cl" in query:
            return [item("www.chileatiende.gob.cl", "ChileAtiende", "Servicios públicos y trámites en Chile")]
        if "site:datos.gob.cl" in query:
            return [item("datos.gob.cl", "Datos abiertos Chile", "Información pública y datos abiertos del Gobierno de Chile")]
        if "site:cplt.cl" in query:
            return [item("www.cplt.cl", "Consejo para la Transparencia", "Transparencia e información pública en Chile")]
        if "site:bcn.cl/leychile" in query:
            return [item("www.bcn.cl", "Ley de Transparencia", "Ley sobre acceso a la información pública en Chile")]
        return [item("www.gob.cl", "Gobierno de Chile", "Información pública del Gobierno de Chile")]

    async def empty_provider(_query, _timeout, _limit):
        return []

    async def exercise():
        orchestrator = WebOrchestrator(
            max_results=5,
            provider_total_seconds=0.1,
            global_budget_seconds=0.8,
            fallback_delay_seconds=0,
        )
        orchestrator.providers = {
            "bing": bing_provider,
            "google": empty_provider,
            "cl0q": empty_provider,
            "mojeek": empty_provider,
            "duckduckgo": empty_provider,
        }
        orchestrator.circuits = {
            name: orchestrator.circuits[name]
            for name in orchestrator.providers
        } | {"yahoo": orchestrator.circuits["yahoo"]}
        return await orchestrator.search(
            "sitio gubernamental Chile información pública",
            fresh=True,
        )

    result = asyncio.run(exercise())

    assert result["ok"] is True, result
    assert result["verification"]["distinct_domains"] >= 3
    assert any("site:chileatiende.gob.cl" in query for query in calls)
    assert any("site:datos.gob.cl" in query for query in calls)
    assert any("site:cplt.cl" in query for query in calls)
    assert any("site:bcn.cl/leychile" in query for query in calls)

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


def test_yahoo_search_parses_general_web_destinations(monkeypatch):
    import tools.web_search as module

    page = """
    <html><body>
      <div class="algo">
        <h3><a href="https://en.wikipedia.org/wiki/Asyncio">asyncio - Wikipedia</a></h3>
        <p>Python asyncio reference and examples.</p>
      </div>
      <div class="algo">
        <a href="https://docs.python.org/3/library/asyncio.html">
          <h3>asyncio — Asynchronous I/O</h3>
        </a>
      </div>
      <div><h3><a href="https://search.yahoo.com/preferences">Search preferences</a></h3></div>
    </body></html>
    """

    class FakeClient:
        async def get(self, url, **kwargs):
            assert url == "https://search.yahoo.com/search"
            assert kwargs["params"]["p"] == "Wikipedia asyncio Python"
            return httpx.Response(
                200,
                text=page,
                request=httpx.Request("GET", url),
            )

    async def fake_client():
        return FakeClient()

    monkeypatch.setattr(module, "_search_http_client", fake_client)
    results = asyncio.run(
        module._yahoo_search(
            "Wikipedia asyncio Python",
            2.0,
            5,
            preserve_candidates=True,
        )
    )

    assert len(results) == 2
    assert results[0]["title"] == "asyncio - Wikipedia"
    assert results[0]["url"] == "https://en.wikipedia.org/wiki/Asyncio"
    assert any(item["url"].startswith("https://docs.python.org/") for item in results)


def test_orchestrator_uses_yahoo_for_generic_web_queries_when_other_providers_are_empty():
    from backend.search.resilient import WebOrchestrator

    candidates = [
        {
            "title": "asyncio - Wikipedia",
            "url": "https://en.wikipedia.org/wiki/Asyncio",
            "snippet": "Python asyncio reference and asynchronous input output.",
        },
        {
            "title": "asyncio — Asynchronous I/O",
            "url": "https://docs.python.org/3/library/asyncio.html",
            "snippet": "Python asyncio documentation and event loop APIs.",
        },
        {
            "title": "Async IO in Python",
            "url": "https://realpython.com/async-io-python/",
            "snippet": "A practical guide to asynchronous programming with Python asyncio.",
        },
    ]

    async def empty_provider(_query, _timeout, _limit):
        return []

    async def yahoo_provider(_query, _timeout, _limit):
        return candidates

    async def exercise():
        orchestrator = WebOrchestrator(
            max_results=5,
            provider_total_seconds=0.15,
            global_budget_seconds=0.5,
            fallback_delay_seconds=0,
        )
        for provider in tuple(orchestrator.providers):
            orchestrator.providers[provider] = empty_provider
        orchestrator.fallback_providers["yahoo"] = yahoo_provider
        return await orchestrator.search("Wikipedia asyncio Python", fresh=True)

    result = asyncio.run(exercise())
    assert result["ok"] is True, result
    assert "yahoo" in result["providers"]
    assert any("wikipedia.org" in item["url"] for item in result["results"])
    assert result["verification"]["distinct_domains"] >= 3


def test_orchestrator_uses_yahoo_when_general_search_has_only_two_domains():
    from backend.search.resilient import WebOrchestrator

    partial_results = [
        {
            "title": "Chile government public data portal",
            "url": "https://datos.gob.cl/",
            "snippet": "Government public information and open data services in Chile.",
        },
        {
            "title": "Chile government transparency and public information",
            "url": "https://www.gobiernotransparentechile.cl/",
            "snippet": "Public government transparency resources for Chile.",
        },
    ]
    yahoo_results = [
        {
            "title": "ChileAtiende government public services",
            "url": "https://www.chileatiende.gob.cl/",
            "snippet": "Official Chilean government portal with public services and information.",
        }
    ]
    yahoo_calls = {"count": 0}
    yahoo_queries = []

    async def two_domains(_query, _timeout, _limit):
        return partial_results

    async def yahoo_provider(query, _timeout, _limit):
        yahoo_calls["count"] += 1
        yahoo_queries.append(query)
        return yahoo_results

    async def exercise():
        orchestrator = WebOrchestrator(
            max_results=5,
            provider_total_seconds=0.15,
            global_budget_seconds=0.5,
            fallback_delay_seconds=0,
        )
        for provider in tuple(orchestrator.providers):
            orchestrator.providers[provider] = two_domains
        orchestrator.fallback_providers["yahoo"] = yahoo_provider
        return await orchestrator.search(
            "sitio gubernamental Chile información pública",
            fresh=True,
        )

    result = asyncio.run(exercise())
    assert yahoo_calls["count"] == 1
    assert "ChileAtiende" in yahoo_queries[0]
    assert "gob.cl" in yahoo_queries[0]
    assert result["ok"] is True, result
    assert "yahoo" in result["providers"]
    assert result["verification"]["distinct_domains"] >= 3


def test_rss_publisher_metadata_preserves_real_domains_behind_google_news_links():
    import tools.web_search as module
    from backend.search.engine import deduplicate, rank_results

    feed = """<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0"><channel>
      <item>
        <title>Política en Chile hoy: gobierno anuncia medidas</title>
        <link>https://news.google.com/rss/articles/article-emol</link>
        <description>Política y gobierno de Chile, medidas públicas anunciadas hoy.</description>
        <source url="https://www.emol.com/">Emol</source>
      </item>
      <item>
        <title>Política de Chile: Congreso y gobierno informan cambios</title>
        <link>https://news.google.com/rss/articles/article-latercera</link>
        <description>Últimas noticias de política y gobierno en Chile.</description>
        <source url="https://www.latercera.com/">La Tercera</source>
      </item>
      <item>
        <title>Gobierno de Chile comunica nuevas medidas políticas</title>
        <link>https://news.google.com/rss/articles/article-biobio</link>
        <description>Información actual de Chile y del gobierno.</description>
        <source url="https://www.biobiochile.cl/">BioBioChile</source>
      </item>
    </channel></rss>"""

    response = httpx.Response(
        200,
        text=feed,
        request=httpx.Request("GET", "https://news.google.com/rss/search"),
    )
    parsed = asyncio.run(module._parse_bing_rss(response, 10))
    assert [item["source_domain"] for item in parsed] == [
        "www.emol.com",
        "www.latercera.com",
        "www.biobiochile.cl",
    ]
    assert all(item["url"].startswith("https://news.google.com/") for item in parsed)

    ranked = rank_results("política Chile hoy últimas noticias", parsed, realtime=True)
    selected = deduplicate(ranked, 5)
    assert len({item["source_domain"] for item in selected}) >= 3


def test_google_news_fallback_uses_expanded_provider_query(monkeypatch):
    import backend.search.resilient as module
    from backend.search.resilient import WebOrchestrator

    query = "política Chile hoy últimas noticias"
    observed = []

    async def fake_google_news(search_query, timeout, limit):
        observed.append((search_query, timeout, limit))
        return []

    monkeypatch.setattr(module, "_google_news_search", fake_google_news)
    orchestrator = WebOrchestrator(
        max_results=5,
        provider_total_seconds=0.2,
        global_budget_seconds=0.8,
        fallback_delay_seconds=0,
    )
    result = asyncio.run(orchestrator._google_news(query, 0.2, 5))

    assert result == []
    assert observed == [
        (
            "Chile política gobierno Congreso elecciones actualidad noticias "
            "BioBioChile Cooperativa Emol La Tercera 24Horas CNN Chile",
            0.2,
            5,
        )
    ]


def test_provider_query_preserves_country_and_website_intent():
    from backend.search.resilient import WebOrchestrator

    query = "sitio comercial tecnología Chile"
    provider_query = WebOrchestrator._provider_query(query)

    assert "chile" in provider_query.casefold()
    assert "commercial" in provider_query.casefold()
    assert "technology" in provider_query.casefold()
    assert "website" in provider_query.casefold()
    assert provider_query.casefold() != "comercial technology"


def test_realtime_football_query_matches_english_and_spanish_publisher_terms():
    from backend.search.engine import rank_results
    from backend.search.resilient import WebOrchestrator

    query = "fútbol internacional resultados hoy"
    recovery = WebOrchestrator._recovery_query(query)
    assert recovery == "international football soccer results scores fixtures standings today"

    results = [
        {
            "title": "International soccer scores and results",
            "url": "https://www.espn.com/soccer/scores",
            "snippet": "Latest international football results, fixtures, standings and scores.",
        },
        {
            "title": "Resultados de fútbol internacional",
            "url": "https://www.ole.com.ar/futbol-internacional/resultados",
            "snippet": "Últimos resultados y marcadores de fútbol internacional.",
        },
        {
            "title": "Weather in Santiago",
            "url": "https://weather.example/santiago",
            "snippet": "Current temperature and weather forecast.",
        },
    ]
    ranked = rank_results(query, results, realtime=True)
    assert len(ranked) >= 2
    assert all(any(word in (item.get("title", "") + " " + item.get("snippet", "")).casefold()
                   for word in ("football", "fútbol", "soccer")) for item in ranked)


def test_chile_public_information_recovery_expands_official_domains():
    from backend.search.engine import rank_results
    from backend.search.resilient import WebOrchestrator

    query = "sitio gubernamental Chile información pública"
    recovery = WebOrchestrator._recovery_query(query)
    folded = recovery.casefold()

    assert "chileatiende" in folded
    assert "datos.gob.cl" in folded
    assert "gob.cl" in folded
    assert "cplt.cl" in folded

    candidates = [
        {
            "title": "ChileAtiende",
            "url": "https://www.chileatiende.gob.cl/",
            "snippet": "Servicios y trámites del Estado.",
        },
        {
            "title": "Datos abiertos",
            "url": "https://datos.gob.cl/",
            "snippet": "Catálogo de datos públicos.",
        },
        {
            "title": "Consejo para la Transparencia",
            "url": "https://www.cplt.cl/",
            "snippet": "Portal institucional y transparencia.",
        },
    ]
    ranked = rank_results(query, candidates, realtime=False)
    domains = {item.get("source_domain") for item in ranked}
    assert len(domains) >= 3



def test_chile_commercial_technology_recovery_preserves_country_and_topic():
    from backend.search.resilient import WebOrchestrator

    recovery = WebOrchestrator._recovery_query("sitio comercial tecnología Chile")
    folded = recovery.casefold()

    assert "chile" in folded
    assert "technology" in folded
    assert "companies" in folded


def test_commercial_technology_site_ranking_rejects_social_network_profiles():
    from backend.search.engine import rank_results

    query = "sitio comercial tecnología Chile"
    candidates = [
        {
            "title": "Empresa de tecnología en Chile",
            "url": "https://www.facebook.com/exampletech",
            "snippet": "Tecnología comercial en Chile",
        },
        {
            "title": "Empresa de tecnología en Chile",
            "url": "https://exampletech.cl/",
            "snippet": "Sitio comercial con soluciones de tecnología para empresas en Chile",
        },
    ]

    ranked = rank_results(query, candidates, realtime=False)

    assert ranked
    assert all("facebook.com" not in item["source_domain"] for item in ranked)

def test_wikipedia_asyncio_recovery_broadens_to_independent_python_sources():
    from backend.search.resilient import WebOrchestrator

    query = "Wikipedia asyncio Python"
    recovery = WebOrchestrator._recovery_query(query)

    assert recovery == "Python asyncio"
    assert recovery.casefold() != query.casefold()


def test_orchestrator_adds_independent_provider_when_domains_are_many_but_provider_is_single():
    from backend.search.resilient import WebOrchestrator

    query = "latest artificial intelligence research and technology news today"
    bing_results = [
        {
            "title": f"Artificial intelligence research technology news today {index}",
            "url": f"https://ai-source-{index}.example.com/article",
            "snippet": "Latest artificial intelligence research and technology news today.",
        }
        for index in range(5)
    ]
    yahoo_results = [
        {
            "title": "Artificial intelligence research technology news today independent",
            "url": "https://independent-ai.example.org/article",
            "snippet": "Latest artificial intelligence research and technology news today.",
        }
    ]
    yahoo_calls = {"count": 0}

    async def bing_provider(_query, _timeout, _limit):
        return bing_results

    async def empty_provider(_query, _timeout, _limit):
        return []

    async def yahoo_provider(_query, _timeout, _limit):
        yahoo_calls["count"] += 1
        return yahoo_results

    async def exercise():
        orchestrator = WebOrchestrator(
            max_results=8,
            provider_total_seconds=0.15,
            global_budget_seconds=0.7,
            fallback_delay_seconds=0,
        )
        for provider in tuple(orchestrator.providers):
            orchestrator.providers[provider] = empty_provider
        orchestrator.providers["bing"] = bing_provider
        orchestrator.fallback_providers["yahoo"] = yahoo_provider
        return await orchestrator.search(query, fresh=True)

    result = asyncio.run(exercise())
    assert result["ok"] is True, result
    assert yahoo_calls["count"] == 1
    assert result["verification"]["distinct_domains"] >= 3
    assert result["verification"]["distinct_providers"] >= 2
    assert {"bing", "yahoo"}.issubset(set(result["providers"]))


def test_wikipedia_asyncio_ranking_accepts_independent_async_io_sources():
    from backend.search.engine import rank_results

    query = "Wikipedia asyncio Python"
    candidates = [
        {
            "title": "asyncio — Asynchronous I/O — Python documentation",
            "url": "https://docs.python.org/3/library/asyncio.html",
            "snippet": "Python asyncio library for asynchronous I/O.",
        },
        {
            "title": "Async IO in Python: A Complete Walkthrough",
            "url": "https://realpython.com/async-io-python/",
            "snippet": "Learn asynchronous IO and Python concurrency.",
        },
        {
            "title": "Asyncio in Python",
            "url": "https://www.geeksforgeeks.org/python/asyncio-in-python/",
            "snippet": "Python asyncio tutorial and examples.",
        },
    ]

    ranked = rank_results(query, candidates, realtime=False)
    domains = {item.get("source_domain") for item in ranked}
    assert len(domains) >= 3
    assert "docs.python.org" in domains
    assert "realpython.com" in domains


def test_commercial_chile_site_intent_treats_commercial_as_shape_not_topic():
    from backend.search.engine import rank_results

    query = "sitio comercial tecnología Chile"
    candidates = [
        {
            "title": "Soluciones de tecnología para empresas",
            "url": "https://tecnologia-ejemplo.cl/",
            "snippet": "Software, nube y servicios tecnológicos para organizaciones.",
        },
        {
            "title": "Technology solutions",
            "url": "https://foreign-example.com/",
            "snippet": "Enterprise software and cloud services.",
        },
    ]

    ranked = rank_results(query, candidates, realtime=False)
    domains = {item.get("source_domain") for item in ranked}
    assert "tecnologia-ejemplo.cl" in domains
    assert "foreign-example.com" not in domains
