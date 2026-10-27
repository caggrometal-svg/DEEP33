from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import httpx

from urllib.parse import urlparse

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
            "published_at": datetime.now(timezone.utc).isoformat(),
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
            "noticias actuales de la comuna de Las Condes en Chile",
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

    targets = {
        "site:chileatiende.gob.cl": item("www.chileatiende.gob.cl", "ChileAtiende", "Servicios públicos y trámites en Chile"),
        "site:datos.gob.cl": item("datos.gob.cl", "Datos abiertos Chile", "Información pública y datos abiertos del Gobierno de Chile"),
        "site:cplt.cl": item("www.cplt.cl", "Consejo para la Transparencia", "Transparencia e información pública en Chile"),
        "site:bcn.cl/leychile": item("www.bcn.cl", "Ley de Transparencia", "Ley sobre acceso a la información pública en Chile"),
    }

    def provider_for(provider_name):
        async def provider(query, _timeout, _limit):
            calls.append((provider_name, query))
            for marker, result in targets.items():
                if marker in query.casefold():
                    return [result]
            if provider_name == "bing":
                return [item("www.gob.cl", "Gobierno de Chile", "Información pública del Gobierno de Chile")]
            return []
        return provider

    async def exercise():
        orchestrator = WebOrchestrator(
            max_results=5,
            provider_total_seconds=0.1,
            global_budget_seconds=0.8,
            fallback_delay_seconds=0,
        )
        orchestrator.providers = {
            name: provider_for(name)
            for name in ("bing", "google", "google_news", "cl0q", "mojeek", "duckduckgo")
        }
        orchestrator.fallback_providers["yahoo"] = provider_for("yahoo")
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
    assert any(provider == "bing" and "site:chileatiende.gob.cl" in query for provider, query in calls)
    assert any(provider == "google" and "site:datos.gob.cl" in query for provider, query in calls)
    assert any(provider == "duckduckgo" and "site:cplt.cl" in query for provider, query in calls)
    assert any(provider == "cl0q" and "site:bcn.cl/leychile" in query for provider, query in calls)


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
    assert "site:datos.gob.cl" in yahoo_queries[0]
    assert "gob.cl" in yahoo_queries[0]
    assert result["ok"] is True, result
    assert "yahoo" in result["providers"]
    assert result["verification"]["distinct_domains"] >= 3


def test_rss_publisher_metadata_preserves_real_domains_behind_google_news_links():
    import tools.web_search as module
    from backend.search.engine import deduplicate, rank_results

    pub_date = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
    feed = f"""<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0"><channel>
      <item>
        <title>Política en Chile hoy: gobierno anuncia medidas</title>
        <link>https://news.google.com/rss/articles/article-emol</link>
        <description>Política y gobierno de Chile, medidas públicas anunciadas hoy.</description>
        <source url="https://www.emol.com/">Emol</source>
        <pubDate>{pub_date}</pubDate>
      </item>
      <item>
        <title>Política de Chile: Congreso y gobierno informan cambios</title>
        <link>https://news.google.com/rss/articles/article-latercera</link>
        <description>Últimas noticias de política y gobierno en Chile.</description>
        <source url="https://www.latercera.com/">La Tercera</source>
        <pubDate>{pub_date}</pubDate>
      </item>
      <item>
        <title>Gobierno de Chile comunica nuevas medidas políticas</title>
        <link>https://news.google.com/rss/articles/article-biobio</link>
        <description>Información actual de Chile y del gobierno.</description>
        <source url="https://www.biobiochile.cl/">BioBioChile</source>
        <pubDate>{pub_date}</pubDate>
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
            "published_at": datetime.now(timezone.utc).isoformat(),
        }
        for index in range(5)
    ]
    yahoo_results = [
        {
            "title": "Artificial intelligence research technology news today independent",
            "url": "https://independent-ai.example.org/article",
            "snippet": "Latest artificial intelligence research and technology news today.",
            "published_at": datetime.now(timezone.utc).isoformat(),
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


def test_named_health_organization_allows_independent_topic_corroboration():
    from backend.search.engine import rank_results
    from backend.search.resilient import WebOrchestrator

    query = "World Health Organization dengue fact sheet"
    recovery = WebOrchestrator._recovery_query(query)
    assert "dengue" in recovery.casefold()
    assert "who" in recovery.casefold()
    assert "cdc" in recovery.casefold()
    assert "paho" in recovery.casefold()

    candidates = [
        {
            "title": "Dengue and severe dengue",
            "url": "https://www.who.int/news-room/fact-sheets/detail/dengue-and-severe-dengue",
            "snippet": "World Health Organization fact sheet on dengue.",
        },
        {
            "title": "About Dengue",
            "url": "https://www.cdc.gov/dengue/about/index.html",
            "snippet": "Public health information about dengue infection and prevention.",
        },
        {
            "title": "Dengue",
            "url": "https://www.paho.org/en/topics/dengue",
            "snippet": "Regional public health information about dengue.",
        },
    ]
    ranked = rank_results(query, candidates, realtime=False)
    domains = {item.get("source_domain") for item in ranked}
    assert {"www.who.int", "www.cdc.gov", "www.paho.org"}.issubset(domains)



def test_chile_commercial_technology_search_uses_real_company_websites():
    from backend.search.resilient import WebOrchestrator

    calls = []
    company_results = {
        "sonda": {
            "title": "SONDA soluciones TI y tecnología digital",
            "url": "https://www.sonda.com/es",
            "snippet": "Servicios de tecnología, cloud, ciberseguridad y soluciones digitales para empresas.",
            "source_domain": "www.sonda.com",
        },
        "entel": {
            "title": "Soluciones tecnológicas para empresas Entel",
            "url": "https://www.entel.cl/corporaciones/sdwan",
            "snippet": "Solución tecnológica para empresas, conectividad segura y servicios digitales en Chile.",
            "source_domain": "www.entel.cl",
        },
        "wom": {
            "title": "WOM canales digitales e internet",
            "url": "https://www.wom.cl/",
            "snippet": "Servicios móviles, internet y canales digitales para clientes en Chile.",
            "source_domain": "www.wom.cl",
        },
        "movistar": {
            "title": "Movistar Empresas soluciones digitales y tecnología",
            "url": "https://ww2.movistar.cl/empresas/comunidad/soluciones-digitales-para-tu-negocio/",
            "snippet": "Soluciones tecnológicas, servicios digitales, cloud y conectividad para negocios en Chile.",
            "source_domain": "ww2.movistar.cl",
        },
    }
    press_results = [
        {
            "title": "Technology energy announcement in Chile",
            "url": "https://energy.einnews.com/article/technology-chile",
            "snippet": "Technology development and energy announcement in Chile.",
            "source_domain": "energy.einnews.com",
        },
        {
            "title": "Technology press release Chile",
            "url": "https://www.prnewswire.com/news-releases/technology-chile.html",
            "snippet": "Commercial technology announcement for Chile.",
            "source_domain": "www.prnewswire.com",
        },
    ]

    def make_provider(name):
        async def provider(query, _timeout, _limit):
            calls.append((name, query))
            for marker, item in company_results.items():
                if marker in query.casefold():
                    return [item]
            return press_results
        return provider

    async def exercise():
        orchestrator = WebOrchestrator(
            max_results=5,
            provider_total_seconds=0.1,
            global_budget_seconds=1.0,
            fallback_delay_seconds=0,
        )
        orchestrator.providers = {
            name: make_provider(name)
            for name in ("bing", "google", "google_news", "cl0q", "mojeek", "duckduckgo")
        }
        orchestrator.fallback_providers["yahoo"] = make_provider("yahoo")
        orchestrator.circuits = {
            name: orchestrator.circuits[name]
            for name in orchestrator.providers
        } | {"yahoo": orchestrator.circuits["yahoo"]}
        return await orchestrator.search("sitio comercial tecnología Chile", fresh=True)

    result = asyncio.run(exercise())
    domains = {str(item.get("url", "")).split("/")[2].removeprefix("www.") for item in result["results"]}
    assert result["ok"] is True, result
    assert len(domains) >= 3, result["results"]
    assert not any("prnewswire.com" in item.get("url", "") or "einnews.com" in item.get("url", "") for item in result["results"])
    assert any("sonda" in query.casefold() for _provider, query in calls)
    assert any("entel" in query.casefold() for _provider, query in calls)
    assert any("wom" in query.casefold() for _provider, query in calls)
    assert any("movistar" in query.casefold() for _provider, query in calls)
    assert not any(query.casefold().lstrip().startswith("site:") for _provider, query in calls)


def test_named_health_organization_search_keeps_who_dengue_fact_sheet():
    from backend.search.resilient import WebOrchestrator

    calls = []
    who = {
        "title": "Dengue and severe dengue",
        "url": "https://www.who.int/news-room/fact-sheets/detail/dengue-and-severe-dengue",
        "snippet": "World Health Organization fact sheet on dengue.",
        "source_domain": "www.who.int",
    }
    cdc = {
        "title": "About Dengue",
        "url": "https://www.cdc.gov/dengue/about/index.html",
        "snippet": "Public health information about dengue infection and prevention.",
        "source_domain": "www.cdc.gov",
    }
    paho = {
        "title": "Dengue",
        "url": "https://www.paho.org/en/topics/dengue",
        "snippet": "Regional public health information about dengue.",
        "source_domain": "www.paho.org",
    }

    def provider_for(name):
        async def provider(query, _timeout, _limit):
            calls.append((name, query))
            if "site:who.int/news-room/fact-sheets/detail" in query:
                return [who]
            if "dengue" in query.casefold():
                return [cdc, paho]
            return []
        return provider

    async def exercise():
        orchestrator = WebOrchestrator(
            max_results=5,
            provider_total_seconds=0.1,
            global_budget_seconds=0.8,
            fallback_delay_seconds=0,
        )
        orchestrator.providers = {
            name: provider_for(name)
            for name in ("bing", "google", "google_news", "cl0q", "mojeek", "duckduckgo")
        }
        orchestrator.fallback_providers["yahoo"] = provider_for("yahoo")
        orchestrator.circuits = {
            name: orchestrator.circuits[name]
            for name in orchestrator.providers
        } | {"yahoo": orchestrator.circuits["yahoo"]}
        return await orchestrator.search("World Health Organization dengue fact sheet", fresh=True)

    result = asyncio.run(exercise())
    assert result["ok"] is True, result
    assert any("site:who.int/news-room/fact-sheets/detail" in query for _provider, query in calls)
    assert any(
        "who.int" in str(item.get("url", "")).casefold() and "dengue" in str(item.get("url", "")).casefold()
        for item in result["results"]
    ), result["results"]

def test_realtime_smartphone_provider_query_is_broad_and_rankable():
    from backend.search.engine import rank_results
    from backend.search.resilient import WebOrchestrator

    query = "producto tecnológico smartphone actual especificaciones"
    provider_query = WebOrchestrator._provider_query(query)
    assert "smartphone" in provider_query.casefold()
    assert "specifications" in provider_query.casefold()
    assert provider_query.casefold() != query.casefold()

    candidates = [
        {
            "title": "Phone specifications and comparison",
            "url": "https://www.gsmarena.com/compare.php3",
            "snippet": "Compare smartphone specifications, models and features.",
        },
        {
            "title": "Smartphone comparison",
            "url": "https://www.phonearena.com/phones/compare",
            "snippet": "Compare current phone specifications and models.",
        },
        {
            "title": "Phone reviews and specifications",
            "url": "https://www.notebookcheck.net/Smartphone-Reviews.127.0.html",
            "snippet": "Current smartphone reviews, specifications and comparisons.",
        },
    ]
    ranked = rank_results(query, candidates, realtime=True)
    assert len({item.get("source_domain") for item in ranked}) >= 3


def test_realtime_smartphone_anchor_ignores_product_shape_words():
    from backend.search.engine import _realtime_anchor_tokens

    assert _realtime_anchor_tokens(
        "producto tecnológico smartphone actual especificaciones"
    ) == {"smartphone"}

    branded = _realtime_anchor_tokens(
        "Samsung Galaxy smartphone actual especificaciones"
    )
    assert "smartphone" in branded
    assert "samsung" in branded
    assert "galaxy" in branded
    assert "specifications" not in branded


def test_chile_economic_recovery_preserves_topic_and_independent_sources():
    from backend.search.resilient import WebOrchestrator

    recovery = WebOrchestrator._recovery_query("noticias económicas Chile hoy")
    folded = recovery.casefold()

    assert "chile" in folded
    assert "econom" in folded
    assert "mercad" in folded
    assert "diario financiero" in folded
    assert "pulso" in folded


def test_python_asyncio_official_intent_launches_targeted_bing_queries():
    from backend.search.resilient import WebOrchestrator

    query = WebOrchestrator._provider_query("Python asyncio official documentation")
    assert "python" in query.casefold()
    assert "asyncio" in query.casefold()


def test_chile_economic_recovery_uses_real_source_oriented_terms():
    from backend.search.resilient import WebOrchestrator

    recovery = WebOrchestrator._recovery_query("noticias económicas Chile hoy").casefold()
    assert "diario financiero" in recovery
    assert "pulso" in recovery
    assert "banco central" in recovery


def test_chile_current_political_news_recovers_independent_domains() -> None:
    from backend.search.resilient import WebOrchestrator

    base_results = [
        {
            "title": "Política de Chile: el gobierno presenta medidas hoy",
            "url": "https://elpais.com/chile/politica/medidas.html",
            "snippet": "Noticias actuales de política y gobierno de Chile.",
        },
        {
            "title": "Gobierno de Chile informa cambios políticos hoy",
            "url": "https://www.gob.cl/noticias/cambios",
            "snippet": "Política pública, Congreso y gobierno de Chile hoy.",
        },
    ]
    source_results = {
        "biobiochile.cl": {
            "title": "Política Chile: gobierno y Congreso anuncia medidas",
            "url": "https://www.biobiochile.cl/noticias/nacional/chile/politica.shtml",
            "snippet": "Actualidad política del gobierno de Chile y el Congreso hoy.",
        },
        "emol.com": {
            "title": "Política en Chile: últimas noticias del gobierno",
            "url": "https://www.emol.com/noticias/Nacional/Politica.html",
            "snippet": "Noticias de política chilena, gobierno y Congreso de hoy.",
        },
        "cooperativa.cl": {
            "title": "Gobierno y política de Chile: cobertura actual",
            "url": "https://cooperativa.cl/noticias/pais/politica/",
            "snippet": "Actualidad política y noticias del gobierno de Chile.",
        },
        "latercera.com": {
            "title": "Chile hoy: noticias de política y gobierno",
            "url": "https://www.latercera.com/politica/",
            "snippet": "Noticias políticas actuales de Chile, gobierno y Congreso.",
        },
    }
    calls: list[tuple[str, str]] = []

    def provider_for(name: str):
        async def provider(query, _timeout, _limit):
            calls.append((name, query))
            lowered_query = query.casefold()
            for host, result in source_results.items():
                if f"site:{host}" in lowered_query:
                    return [result]
            return base_results
        return provider

    async def no_yahoo(_query, _timeout, _limit):
        return []

    async def exercise():
        orchestrator = WebOrchestrator(
            max_results=5,
            provider_total_seconds=0.15,
            global_budget_seconds=0.8,
            fallback_delay_seconds=0,
        )
        orchestrator.providers = {
            name: provider_for(name)
            for name in ("bing", "google", "google_news", "cl0q", "mojeek", "duckduckgo")
        }
        orchestrator.fallback_providers["yahoo"] = no_yahoo
        return await orchestrator.search(
            "política Chile hoy últimas noticias",
            fresh=True,
        )

    result = asyncio.run(exercise())
    result_domains = {
        str(item.get("source_domain") or urlparse(str(item.get("url") or "")).hostname or "").casefold()
        for item in result["results"]
    }
    assert result["ok"] is True, result
    assert len(result_domains - {""}) >= 3, result["results"]
    assert result["verification"]["distinct_domains"] >= 3
    assert any("site:" in query for _provider, query in calls)
    assert all(
        any(host in item.get("url", "") for host in ("elpais.com", "gob.cl", "biobiochile.cl", "emol.com", "cooperativa.cl", "latercera.com"))
        for item in result["results"]
    )


def test_chile_current_political_news_rejects_generic_landing_pages() -> None:
    from backend.search.engine import deduplicate, rank_results

    query = "política Chile hoy últimas noticias"
    candidates = [
        {
            "title": "Gobierno de Chile - Gob.cl",
            "url": "https://www.gob.cl/",
            "snippet": "",
            "provider": "bing",
        },
        {
            "title": "Gobierno Regional De Aysén - Gobierno Regional De Aysén",
            "url": "https://www.goreaysen.cl/",
            "snippet": "",
            "provider": "bing",
        },
        {
            "title": "Diputados presentan reforma política en Chile",
            "url": "https://www.biobiochile.cl/noticias/nacional/chile/politica/diputados-reforma.shtml",
            "snippet": "La Cámara discutirá el proyecto del gobierno durante la sesión de hoy en el Congreso nacional.",
            "provider": "bing",
            "published_at": datetime.now(timezone.utc).isoformat(),
        },
        {
            "title": "Oposición cuestiona nueva propuesta política del gobierno chileno",
            "url": "https://www.emol.com/noticias/Nacional/2026/10/14/oposicion-gobierno.shtml",
            "snippet": "La oposición criticó la medida presentada hoy y pidió explicaciones a los ministros del gobierno.",
            "provider": "google_news",
            "published_at": datetime.now(timezone.utc).isoformat(),
        },
        {
            "title": "Senadores debaten nueva ley política en Chile",
            "url": "https://cooperativa.cl/noticias/pais/politica/senadores-debaten-ley/2026-10-14/120000.html",
            "snippet": "La comisión debatió la iniciativa y los parlamentarios esperan una votación durante esta jornada.",
            "provider": "duckduckgo",
            "published_at": datetime.now(timezone.utc).isoformat(),
        },
    ]

    ranked = rank_results(query, candidates, realtime=True)
    selected = deduplicate(ranked, 5)
    domains = {
        str(item.get("source_domain") or "")
        for item in selected
    }

    assert len(domains) >= 3, selected
    assert all(item.get("url") not in {
        "https://www.gob.cl/",
        "https://www.goreaysen.cl/",
    } for item in selected)
    assert any("biobiochile.cl" in item.get("url", "") for item in selected)
    assert any("emol.com" in item.get("url", "") for item in selected)
    assert any("cooperativa.cl" in item.get("url", "") for item in selected)


def test_google_news_keeps_explicit_publisher_site_scope(monkeypatch) -> None:
    import backend.search.resilient as module
    from backend.search.resilient import WebOrchestrator

    seen: list[str] = []

    async def fake_google_news_search(query, _timeout, _limit):
        seen.append(query)
        return []

    monkeypatch.setattr(module, "_google_news_search", fake_google_news_search)
    orchestrator = WebOrchestrator(
        provider_total_seconds=0.1,
        global_budget_seconds=0.3,
        fallback_delay_seconds=0,
    )

    asyncio.run(
        orchestrator._google_news(
            "site:emol.com Chile política gobierno Congreso noticias hoy",
            0.1,
            5,
        )
    )

    assert seen == ["site:emol.com Chile política gobierno Congreso noticias hoy"]


def test_chile_commercial_technology_recovery_uses_broad_brand_queries():
    from backend.search.resilient import _commercial_technology_queries

    queries = _commercial_technology_queries()
    assert len(queries) >= 4
    assert all(not query.casefold().lstrip().startswith("site:") for _, query in queries)
    corpus = " ".join(query.casefold() for _, query in queries)
    assert all(brand in corpus for brand in ("sonda", "entel", "wom", "movistar"))
    assert len({provider for provider, _ in queries}) == len(queries)



def test_search_web_propagates_non_fast_budget_to_orchestrator(monkeypatch):
    from tools import web_search

    seen = {}

    class FakeOrchestrator:
        async def search(self, query, *, fresh=False, budget_seconds=None):
            seen["query"] = query
            seen["fresh"] = fresh
            seen["budget_seconds"] = budget_seconds
            return {
                "ok": True,
                "results": [{
                    "title": "Example result",
                    "url": "https://example.com/article",
                    "snippet": "Relevant example result.",
                    "source_domain": "example.com",
                    "provider": "fake",
                }],
                "sources": [],
                "providers": ["fake"],
                "providers_attempted": ["fake"],
                "errors": [],
            }

    monkeypatch.setattr(
        "backend.search.resilient.get_web_orchestrator",
        lambda _limit: FakeOrchestrator(),
    )
    monkeypatch.setattr(
        "backend.search.resilient.WebOrchestrator",
        FakeOrchestrator,
    )

    result = asyncio.run(web_search.search_web(
        "current example topic",
        timeout_seconds=4.75,
        max_results=5,
        fresh=True,
    ))

    assert result["ok"] is True
    assert seen["fresh"] is True
    assert seen["budget_seconds"] == 4.75


def test_known_reference_query_uses_general_web_search_fanout(monkeypatch):
    import backend.search.resilient as resilient
    from backend.search.resilient import WebOrchestrator
    from tools import web_search

    calls = []

    class FakeOrchestrator(WebOrchestrator):
        async def search(self, query, *, fresh=False, budget_seconds=None):
            calls.append(query)
            return {
                "ok": True,
                "status": "OK",
                "query": query,
                "realtime": False,
                "providers": ["bing", "google"],
                "provider": "bing",
                "winner_provider": "bing",
                "results": [
                    {
                        "title": "Asyncio general web result",
                        "url": "https://example.org/asyncio",
                        "snippet": "Independent general-web result for the requested topic.",
                        "source_domain": "example.org",
                    }
                ],
                "sources": [],
                "verification": {
                    "level": "multi-provider",
                    "distinct_domains": 1,
                    "distinct_providers": 2,
                    "relevant_results": 1,
                },
                "errors": [],
            }

    orchestrator = FakeOrchestrator(max_results=5)
    monkeypatch.setattr(
        resilient,
        "get_web_orchestrator",
        lambda _max_results: orchestrator,
    )

    result = asyncio.run(
        web_search.search_web(
            "python asyncio official documentation",
            timeout_seconds=2.0,
            max_results=5,
            fresh=True,
        )
    )

    assert calls == ["python asyncio official documentation"]
    assert result["providers"] == ["bing", "google"]
    assert result["results"][0]["url"] == "https://example.org/asyncio"
    assert result["fresh_request"] is True

def test_spanish_python_release_query_rejects_off_topic_debian_page():
    from backend.search.engine import is_realtime_query, rank_results

    query = (
        "Busca en Internet cuál es la versión estable más reciente de Python publicada oficialmente. "
        "Contrasta las páginas oficiales recuperadas y cita enlaces directos."
    )
    unrelated = {
        "title": "Debian -- Versiones de Debian",
        "url": "https://www.debian.org/releases/index.es.html",
        "snippet": "Versiones estables y documentación de Debian GNU/Linux.",
        "source_domain": "www.debian.org",
    }
    relevant = {
        "title": "Python Release Python 3.15.1",
        "url": "https://www.python.org/downloads/release/python-3151/",
        "snippet": "Python 3.15.1 is the latest stable release of Python.",
        "source_domain": "www.python.org",
    }

    assert is_realtime_query(query)
    assert rank_results(query, [unrelated], realtime=True) == []
    ranked = rank_results(query, [unrelated, relevant], realtime=True)
    assert [item["url"] for item in ranked] == [relevant["url"]]


def test_release_version_recovery_broadens_beyond_official_domain():
    from backend.search.resilient import WebOrchestrator

    query = "latest stable Python release official documentation"
    primary = WebOrchestrator._provider_query(query)
    recovery = WebOrchestrator._recovery_query(query)

    assert recovery.casefold() != primary.casefold()
    assert "python" in recovery.casefold()
    assert "independent" in recovery.casefold()
    assert "version" in recovery.casefold()
