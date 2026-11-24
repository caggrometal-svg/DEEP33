from __future__ import annotations
import asyncio
import base64
from datetime import datetime, timezone
import httpx
import pytest
from tools.web_fetch import SSRFBlockedError, validate_public_url
from tools.web_search import search_web

def test_ssrf_blocks_private_hosts(monkeypatch):
    for url in ["http://127.0.0.1/","http://localhost/","http://10.0.0.1/","http://192.168.1.1/","http://169.254.169.254/","http://metadata.google.internal/"]:
        with pytest.raises(SSRFBlockedError): validate_public_url(url)

def test_url_policy():
    with pytest.raises(SSRFBlockedError): validate_public_url("https://user:pass@example.com/")
    with pytest.raises(SSRFBlockedError): validate_public_url("https://example.com:8080/")

def test_resilient_search_normalisation(monkeypatch):
    import backend.search.resilient as resilient_module

    class FakeOrchestrator:
        async def search(self, query, *, fresh=False):
            return {
                "ok": True,
                "engine": "DEEP33 Search Engine",
                "engine_version": "1.4.0",
                "results": [{"title": "DEEP33 Example", "url": "https://example.com/", "snippet": "Snippet"}],
                "provider": "bing",
            }

    monkeypatch.setattr(resilient_module, "get_web_orchestrator", lambda max_results=5: FakeOrchestrator())
    result = asyncio.run(search_web("DEEP33", provider="auto", fresh=True))
    assert result["results"][0]["snippet"] == "Snippet"
    assert result["engine"] == "DEEP33 Search Engine"
    assert result["provider"] == "bing"

def test_tavily_realtime_news_is_time_bounded(monkeypatch):
    import tools.web_search as module

    captured = {}

    class Response:
        status_code = 200
        content = b"{}"

        def json(self):
            return {
                "results": [{
                    "title": "Las Condes current event",
                    "url": "https://example.com/news",
                    "content": "Las Condes incident today",
                    "published_date": "2026-10-08T20:00:00Z",
                }]
            }

    class Client:
        async def post(self, *args, **kwargs):
            captured["json"] = kwargs["json"]
            return Response()

    async def fake_client():
        return Client()

    monkeypatch.setattr(module, "_search_http_client", fake_client)

    result = asyncio.run(
        module._tavily_search(
            "situación actual de Las Condes",
            "tvly-test",
            5,
            5,
        )
    )

    assert captured["json"]["topic"] == "news"
    assert captured["json"]["time_range"] == "day"
    assert captured["json"]["filter_by_published_date"] is True
    assert captured["json"]["include_published_date"] is True
    assert result[0]["published_at"].startswith("2026-10-08")


def test_recent_news_recovery_accepts_bing_click_url_with_verified_publisher_domain(monkeypatch):
    import tools.web_search as module

    published_at = datetime.now(timezone.utc).isoformat()
    wrapped_url = (
        "https://www.bing.com/news/apiclick.aspx?"
        "url=https%3A%2F%2Fwww.biobiochile.cl%2Fnoticias%2F2026%2F11%2F23%2Fnoticia.shtml"
    )

    async def no_google_news(*_args, **_kwargs):
        return []

    async def fake_bing_rss(query, _timeout, _limit):
        if "site:biobiochile.cl" not in query:
            return []
        return [{
            "title": "Últimas noticias de Chile hoy: actualización nacional",
            "url": wrapped_url,
            "snippet": "Noticia reciente de Chile publicada hoy.",
            "source_domain": "www.biobiochile.cl",
            "published_at": published_at,
            "provider": "bing",
        }]

    monkeypatch.setattr(module, "_google_news_search", no_google_news)
    monkeypatch.setattr(module, "_google_news_top_stories_search", no_google_news)
    monkeypatch.setattr(module, "_bing_recent_news_search", fake_bing_rss)
    monkeypatch.setattr(module, "_publisher_rss_search", no_google_news)
    monkeypatch.setattr(module, "_mwmbl_search", no_google_news)

    candidates = asyncio.run(
        module.search_recent_news_candidates(
            "Chile noticias hoy",
            timeout_seconds=1.0,
            max_results=5,
        )
    )

    assert len(candidates) == 1
    assert candidates[0]["source_domain"] == "www.biobiochile.cl"
    assert candidates[0]["provider"] == "bing"
    assert candidates[0]["provider_sources"] == ["bing"]


def test_publisher_rss_fallback_extracts_dated_direct_articles(monkeypatch):
    import tools.web_search as module

    published_at = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
    feeds = {
        "https://www.latercera.com/arcio/rss/": (
            "latercera.com",
            "https://www.latercera.com/politica/noticia/chile-actualidad/ABC123/",
        ),
        "https://www.cooperativa.cl/noticias/site/tax/port/all/rss____1.xml": (
            "cooperativa.cl",
            "https://www.cooperativa.cl/noticias/pais/actualidad/2026/11/23/noticia.shtml",
        ),
    }

    class FakeResponse:
        status_code = 200

        def __init__(self, domain, url):
            self.text = (
                "<rss><channel><item>"
                "<title>Últimas noticias de Chile hoy: actualización nacional</title>"
                f"<link>{url}</link>"
                "<description>Información reciente de Chile y actualidad nacional.</description>"
                f"<pubDate>{published_at}</pubDate>"
                "</item></channel></rss>"
            )
            self.content = self.text.encode()

    class FakeClient:
        async def get(self, url, **_kwargs):
            domain, article_url = feeds[url]
            return FakeResponse(domain, article_url)

    async def fake_http_client():
        return FakeClient()

    monkeypatch.setattr(module, "_search_http_client", fake_http_client)
    monkeypatch.setattr(
        module,
        "PUBLISHER_NEWS_RSS_FEEDS",
        tuple((domain, feed_url) for feed_url, (domain, _article_url) in feeds.items()),
    )

    candidates = asyncio.run(
        module._publisher_rss_search(
            "noticias de Chile hoy",
            timeout_seconds=1.0,
            max_results=5,
        )
    )

    assert len(candidates) == 2
    assert {item["source_domain"] for item in candidates} == {
        "latercera.com",
        "cooperativa.cl",
    }
    assert all(item["provider"] == "publisher_rss" for item in candidates)
    assert all(item["published_at"] for item in candidates)


def test_recent_chile_rss_articles_survive_strict_query_ranking(monkeypatch):
    import backend.main as main

    published_at = datetime.now(timezone.utc).isoformat()
    candidates = [
        {
            "title": "Actualidad nacional: nueva medida del Gobierno",
            "url": "https://www.theclinic.cl/2026/11/23/actualidad-nacional/",
            "source_domain": "theclinic.cl",
            "provider": "publisher_rss",
            "published_at": published_at,
            "snippet": "Información reciente publicada por un medio chileno.",
        },
        {
            "title": "Presupuesto 2027 y cambios del Congreso",
            "url": "https://www.latercera.com/nacional/noticia/presupuesto-2027/",
            "source_domain": "latercera.com",
            "provider": "publisher_rss",
            "published_at": published_at,
            "snippet": "Información reciente sobre Chile y el Congreso.",
        },
        {
            "title": "Noticia de Chile desde índice independiente",
            "url": "https://mwmbl-source.example/noticias/chile-actualidad/",
            "source_domain": "mwmbl-source.example",
            "provider": "mwmbl",
            "snippet": "Información reciente de Chile desde un índice independiente.",
        },
        {
            "title": "Últimas noticias de Chile desde Google News",
            "url": "https://news.google.com/rss/articles/current-chile-article",
            "source_domain": "cooperativa.cl",
            "provider": "google_news",
            "snippet": "Información reciente de Chile y actualidad nacional.",
        },
        {
            "title": "Página genérica de noticias",
            "url": "https://www.chicureohoy.cl/",
            "source_domain": "chicureohoy.cl",
            "provider": "bing",
            "published_at": published_at,
            "snippet": "Portada general.",
        },
    ]

    monkeypatch.setattr(main, "rank_results", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(main, "_is_recently_published", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(main, "score_result", lambda *_args, **_kwargs: 0.1)

    ranked = main._rank_recent_news_candidates("noticias de Chile hoy", candidates)

    assert {item["source_domain"] for item in ranked} == {
        "theclinic.cl",
        "latercera.com",
        "mwmbl-source.example",
        "cooperativa.cl",
    }
    assert {item["provider"] for item in ranked} == {
        "publisher_rss",
        "mwmbl",
        "google_news",
    }


def test_publisher_rss_recovery_round_robins_editorial_domains(monkeypatch):
    import tools.web_search as module

    published_at = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
    feeds = {
        "https://rss.a.example/feed.xml": ("a.example", "https://a.example/news/"),
        "https://rss.b.example/feed.xml": ("b.example", "https://b.example/news/"),
        "https://rss.c.example/feed.xml": ("c.example", "https://c.example/news/"),
    }

    class FakeResponse:
        status_code = 200

        def __init__(self, domain, prefix):
            items = "".join(
                "<item>"
                f"<title>Chile actualidad artículo {index} de {domain}</title>"
                f"<link>{prefix}2026/11/24/article-{index}.html</link>"
                "<description>Noticia reciente de Chile y actualidad nacional.</description>"
                f"<pubDate>{published_at}</pubDate>"
                "</item>"
                for index in range(4)
            )
            self.text = f"<rss><channel>{items}</channel></rss>"
            self.content = self.text.encode()

    class FakeClient:
        async def get(self, url, **_kwargs):
            domain, prefix = feeds[url]
            return FakeResponse(domain, prefix)

    async def fake_http_client():
        return FakeClient()

    monkeypatch.setattr(module, "_search_http_client", fake_http_client)
    monkeypatch.setattr(
        module,
        "PUBLISHER_NEWS_RSS_FEEDS",
        tuple((domain, feed_url) for feed_url, (domain, _prefix) in feeds.items()),
    )

    candidates = asyncio.run(
        module._publisher_rss_search(
            "noticias de Chile hoy",
            timeout_seconds=1.0,
            max_results=5,
        )
    )

    assert len(candidates) == 5
    assert len({item["source_domain"] for item in candidates}) == 3


def test_recent_news_recovery_reserves_slots_for_independent_search_providers(monkeypatch):
    import tools.web_search as module

    published_at = datetime.now(timezone.utc).isoformat()
    publisher_candidates = [
        {
            "title": f"Chile noticia reciente de medio {index}",
            "url": f"https://medio-{index}.example/noticias/chile-{index}.html",
            "snippet": "Noticia reciente de Chile y actualidad nacional.",
            "source_domain": f"medio-{index}.example",
            "provider": "publisher_rss",
            "provider_sources": ["publisher_rss"],
            "published_at": published_at,
        }
        for index in range(1, 6)
    ]

    async def fake_publisher_rss(_query, _timeout, max_results):
        return publisher_candidates[:max_results]

    async def fake_mwmbl(*_args, **_kwargs):
        return [
            {
                "title": "Chile actualidad desde el índice Mwmbl",
                "url": "https://mwmbl-source.example/noticias/chile-mwmbl.html",
                "snippet": "Noticia reciente de Chile desde un índice independiente.",
                "source_domain": "mwmbl-source.example",
                "provider": "mwmbl",
            },
            {
                "title": "Otra noticia de Chile desde Mwmbl",
                "url": "https://mwmbl-other.example/noticias/chile-mwmbl-2.html",
                "snippet": "Otra noticia reciente de Chile desde un índice independiente.",
                "source_domain": "mwmbl-other.example",
                "provider": "mwmbl",
            },
        ]

    async def fake_google_news(*_args, **_kwargs):
        return [{
            "title": "Noticias recientes de Chile desde Google News",
            "url": "https://google-source.example/noticias/chile-google.html",
            "snippet": "Noticia reciente de Chile publicada hoy.",
            "source_domain": "google-source.example",
            "provider": "google_news",
            "published_at": published_at,
        }]

    async def fake_bing_rss(query, _timeout, _limit):
        if "site:biobiochile.cl" not in query:
            return []
        return [{
            "title": "Noticias recientes de Chile desde Bing RSS",
            "url": "https://www.biobiochile.cl/noticias/2026/11/24/chile-bing.shtml",
            "snippet": "Noticia reciente de Chile publicada hoy.",
            "source_domain": "biobiochile.cl",
            "provider": "bing",
            "published_at": published_at,
        }]

    async def no_google_top_stories(*_args, **_kwargs):
        return []

    monkeypatch.setattr(module, "_publisher_rss_search", fake_publisher_rss)
    monkeypatch.setattr(module, "_mwmbl_search", fake_mwmbl)
    monkeypatch.setattr(module, "_google_news_search", fake_google_news)
    monkeypatch.setattr(module, "_google_news_top_stories_search", no_google_top_stories)
    monkeypatch.setattr(module, "_bing_recent_news_search", fake_bing_rss)

    candidates = asyncio.run(
        module.search_recent_news_candidates(
            "noticias de Chile hoy",
            timeout_seconds=1.0,
            max_results=8,
        )
    )

    providers = {item["provider"] for item in candidates}
    assert "publisher_rss" in providers
    assert "mwmbl" in providers
    assert "google_news" in providers
    assert "bing" in providers


def test_recent_news_recovery_retries_google_news_with_short_chile_query(monkeypatch):
    import tools.web_search as module

    published_at = datetime.now(timezone.utc).isoformat()
    seen_queries = []

    async def no_results(*_args, **_kwargs):
        return []

    async def fake_google_news(query, _timeout, _limit):
        seen_queries.append(query)
        if query != "noticias Chile":
            return []
        return [{
            "title": "Noticias recientes de Chile: actualización nacional",
            "url": "https://google-news-source.example/noticias/chile-hoy.html",
            "snippet": "Noticia reciente de Chile publicada hoy.",
            "source_domain": "google-news-source.example",
            "provider": "google_news",
        }]

    monkeypatch.setattr(module, "_publisher_rss_search", no_results)
    monkeypatch.setattr(module, "_mwmbl_search", no_results)
    monkeypatch.setattr(module, "_google_news_search", fake_google_news)
    monkeypatch.setattr(module, "_google_news_top_stories_search", no_results)
    monkeypatch.setattr(module, "_bing_recent_news_search", no_results)

    candidates = asyncio.run(
        module.search_recent_news_candidates(
            "Chile noticias hoy actualidad acontecimientos recientes",
            timeout_seconds=1.0,
            max_results=8,
        )
    )

    assert "noticias Chile" in seen_queries
    assert len(candidates) == 1
    assert candidates[0]["provider"] == "google_news"


def test_resilient_orchestrator_falls_back_to_duckduckgo(monkeypatch):
    from backend.search.resilient import WebOrchestrator

    orchestrator = WebOrchestrator(
        max_results=3,
        provider_total_seconds=0.05,
        global_budget_seconds=0.30,
        fallback_delay_seconds=0.01,
    )

    async def failing(*_args, **_kwargs):
        raise RuntimeError("down")

    async def ddg(*_args, **_kwargs):
        return [
            {"title": "DEEP33 DDG", "url": "https://example.com/", "snippet": "DEEP33 fallback result"},
            {"title": "DEEP33 DDG 2", "url": "https://example.org/", "snippet": "DEEP33 fallback result"},
            {"title": "DEEP33 DDG 3", "url": "https://example.net/", "snippet": "DEEP33 fallback result"},
        ]

    orchestrator.providers["bing"] = failing
    orchestrator.providers["google"] = failing
    orchestrator.providers["mojeek"] = failing
    orchestrator.providers["duckduckgo"] = ddg
    result = asyncio.run(orchestrator.search("DEEP33 fallback test", fresh=True))
    assert result["winner_provider"] == "duckduckgo"

def test_recovery_keeps_success_when_sibling_recovery_provider_is_slow():
    from backend.search.resilient import ProviderOutcome, WebOrchestrator

    orchestrator = WebOrchestrator(
        max_results=3,
        provider_total_seconds=0.05,
        global_budget_seconds=0.25,
        fallback_delay_seconds=0.0,
        cache_ttl_seconds=30,
    )
    recovered = []

    results = [
        {"title": "asyncio — Asynchronous I/O", "url": "https://docs.python.org/3/library/asyncio.html",
         "snippet": "Python asyncio documentation and event loop APIs"},
        {"title": "Python asyncio tutorial", "url": "https://realpython.com/async-io-python/",
         "snippet": "Python asyncio guide and asynchronous programming"},
        {"title": "PEP 3156", "url": "https://peps.python.org/pep-3156/",
         "snippet": "Python asyncio event loop design and APIs"},
    ]

    async def fake_call_provider(provider, query):
        if "official" in query.casefold():
            return ProviderOutcome(provider=provider, ok=False, error_code="NO_RESULTS")
        recovered.append(provider)
        if provider in {"bing", "mojeek"}:
            return ProviderOutcome(provider=provider, ok=True, results=results)
        await asyncio.sleep(0.5)
        return ProviderOutcome(provider=provider, ok=False, error_code="PROVIDER_TIMEOUT")

    orchestrator._call_provider = fake_call_provider
    result = asyncio.run(
        orchestrator.search("Python asyncio official documentation", fresh=True)
    )

    assert result["ok"] is True, result
    assert result["status"] == "OK", result
    assert len({item["source_domain"] for item in result["results"]}) >= 3
    assert "bing" in recovered
    assert result["latency_ms"] < 250


def test_web_orchestrator_never_certifies_a_single_configured_provider():
    from backend.search.resilient import WebOrchestrator

    async def only_provider(query, timeout, limit):
        return [
            {
                "title": "NASA Artemis mission",
                "url": f"https://nasa{i}.example/artemis",
                "snippet": "NASA Artemis mission and lunar exploration.",
            }
            for i in range(3)
        ]

    async def empty_provider(query, timeout, limit):
        return []

    async def exercise():
        orchestrator = WebOrchestrator(
            max_results=5,
            provider_total_seconds=0.05,
            global_budget_seconds=0.18,
            fallback_delay_seconds=0.0,
        )
        orchestrator.providers = {"bing": only_provider}
        orchestrator.fallback_providers = {"yahoo": empty_provider}
        orchestrator.circuits = {
            "bing": orchestrator.circuits["bing"],
            "yahoo": orchestrator.circuits["yahoo"],
        }
        return await orchestrator.search("NASA Artemis mission", fresh=True)

    result = asyncio.run(exercise())
    assert result["ok"] is False, result
    assert result["status"] == "INSUFFICIENT_PROVIDER_QUORUM"
    assert result["providers"] == ["bing"]
    assert result["verification"]["required_providers"] == 2
    assert result["verification"]["provider_quorum_met"] is False


def test_web_orchestrator_uses_reserved_independent_provider_for_sparse_news():
    from datetime import datetime, timezone
    from backend.search.resilient import ProviderOutcome, WebOrchestrator

    published_at = datetime.now(timezone.utc).isoformat()
    results_by_provider = {
        "bing": [{
            "title": "Noticias de Chile hoy: autoridades anuncian medidas",
            "url": "https://news-one.example/chile/actualidad",
            "snippet": "Noticias recientes de Chile hoy sobre autoridades y actualidad nacional.",
            "published_at": published_at,
        }],
        "google": [{
            "title": "Noticias de Chile hoy: Congreso informa cambios",
            "url": "https://news-two.example/chile/congreso",
            "snippet": "Noticias recientes de Chile hoy sobre Congreso y actualidad nacional.",
            "published_at": published_at,
        }],
        "mwmbl": [{
            "title": "Noticias de Chile hoy: nueva información nacional",
            "url": "https://news-three.example/chile/nacional",
            "snippet": "Noticias recientes de Chile hoy con información nacional.",
            "published_at": published_at,
        }],
    }
    recovered = []

    async def fake_call_provider(provider, query):
        if provider == "mwmbl":
            recovered.append(provider)
        items = results_by_provider.get(provider, [])
        return ProviderOutcome(
            provider=provider,
            ok=bool(items),
            results=items,
            error_code=None if items else "NO_RESULTS",
        )

    orchestrator = WebOrchestrator(
        max_results=5,
        provider_total_seconds=0.05,
        global_budget_seconds=0.8,
        fallback_delay_seconds=0.0,
    )
    orchestrator.providers = {
        "bing": orchestrator._bing,
        "google": orchestrator._google,
    }
    orchestrator.fallback_providers = {"mwmbl": orchestrator._mwmbl}
    orchestrator.circuits = {
        name: orchestrator.circuits[name]
        for name in ("bing", "google", "mwmbl")
    }
    orchestrator._call_provider = fake_call_provider

    result = asyncio.run(
        orchestrator.search("últimas noticias de Chile hoy", fresh=True)
    )

    assert recovered == ["mwmbl"]
    assert result["ok"] is True, result
    assert len(result["results"]) >= 3
    assert result["verification"]["distinct_providers"] >= 2
    assert result["verification"]["distinct_domains"] >= 3


def test_search_provider_families_collapse_google_and_bing_aliases():
    from backend.search.resilient import _provider_family

    assert _provider_family("google_news") == _provider_family("google")
    assert _provider_family("yahoo") == _provider_family("bing")
    assert _provider_family("duckduckgo") == _provider_family("bing")


def test_web_orchestrator_does_not_count_google_news_as_a_second_google_provider():
    from backend.search.resilient import WebOrchestrator

    candidates = [
        {
            "title": "NASA Artemis mission lunar exploration",
            "url": f"https://space{i}.example/artemis",
            "snippet": "NASA Artemis mission and lunar exploration research.",
        }
        for i in range(3)
    ]

    async def google_provider(query, timeout, limit):
        return candidates

    async def empty_provider(query, timeout, limit):
        return []

    async def exercise():
        orchestrator = WebOrchestrator(
            max_results=5,
            provider_total_seconds=0.05,
            global_budget_seconds=0.18,
            fallback_delay_seconds=0.0,
        )
        orchestrator.providers = {
            "google": google_provider,
            "google_news": google_provider,
            "bing": empty_provider,
            "mojeek": empty_provider,
        }
        orchestrator.fallback_providers = {"yahoo": empty_provider}
        orchestrator.circuits = {
            name: orchestrator.circuits[name]
            for name in orchestrator.providers
        } | {"yahoo": orchestrator.circuits["yahoo"]}
        return await orchestrator.search("NASA Artemis mission", fresh=True)

    result = asyncio.run(exercise())
    assert result["ok"] is False, result
    assert result["status"] == "INSUFFICIENT_PROVIDER_QUORUM"
    assert set(result["providers"]) == {"google", "google_news"}
    assert result["verification"]["distinct_providers"] == 1
    assert result["verification"]["required_providers"] == 2
    assert result["verification"]["provider_quorum_met"] is False


def test_web_orchestrator_fails_closed_when_only_one_provider_has_relevant_results():
    from backend.search.resilient import ProviderOutcome, WebOrchestrator

    orchestrator = WebOrchestrator(
        max_results=3,
        provider_total_seconds=0.05,
        global_budget_seconds=0.18,
        fallback_delay_seconds=0.0,
        cache_ttl_seconds=30,
    )
    relevant = [
        {"title": "Python asyncio documentation", "url": f"https://docs.python.org/3/asyncio-{i}.html",
         "snippet": "Python asyncio documentation and event loop"}
        for i in range(3)
    ]
    unrelated = [
        {"title": "Gardening guide", "url": "https://gardening.example/soil",
         "snippet": "Plants, soil and garden care"}
    ]

    async def fake_call_provider(provider, query):
        if provider == "bing":
            return ProviderOutcome(provider=provider, ok=True, results=relevant)
        if provider == "mojeek":
            # A transport-successful response is not a relevant evidence provider.
            return ProviderOutcome(provider=provider, ok=True, results=unrelated)
        return ProviderOutcome(provider=provider, ok=False, error_code="NO_RESULTS")

    orchestrator._call_provider = fake_call_provider
    result = asyncio.run(
        orchestrator.search("Python asyncio documentation", fresh=True)
    )

    assert result["results"], result
    assert result["ok"] is False
    assert result["status"] == "INSUFFICIENT_PROVIDER_QUORUM"
    assert result["providers"] == ["bing"]
    assert result["verification"]["distinct_providers"] == 1
    assert result["verification"]["required_providers"] == 2
    assert result["verification"]["provider_quorum_met"] is False


def test_recovery_broadens_provider_query_but_ranks_against_original_question():
    from backend.search.resilient import WebOrchestrator

    orchestrator = WebOrchestrator(
        max_results=5,
        provider_total_seconds=0.08,
        global_budget_seconds=0.4,
        fallback_delay_seconds=0.0,
        cache_ttl_seconds=30,
    )
    seen = []
    candidates = [
        {"title": "asyncio — Asynchronous I/O", "url": "https://docs.python.org/3/library/asyncio.html",
         "snippet": "Python asyncio documentation and event loop APIs"},
        {"title": "Python asyncio tutorial", "url": "https://realpython.com/async-io-python/",
         "snippet": "Python asyncio guide and asynchronous programming"},
        {"title": "Python asyncio event loop", "url": "https://peps.python.org/pep-3156/",
         "snippet": "Python asyncio event loop design and APIs"},
    ]

    async def provider(query, _timeout, _limit):
        seen.append(query)
        if query == "python asyncio":
            return candidates
        return []

    orchestrator.providers["bing"] = provider
    orchestrator.providers["google"] = provider
    orchestrator.providers["mojeek"] = provider
    orchestrator.providers["duckduckgo"] = provider
    original = "Python asyncio TaskGroup documentación oficial"
    result = asyncio.run(orchestrator.search(original, fresh=True))

    assert result["ok"] is True, result
    assert result["query"] == original
    assert result["queries_executed"] == ["python asyncio"]
    assert len({item["source_domain"] for item in result["results"]}) >= 3
    assert "python asyncio taskgroup official" in seen
    assert "python asyncio" in seen


def test_no_results_logs_redacted_rank_diagnostics(caplog):
    from backend.search.resilient import WebOrchestrator

    orchestrator = WebOrchestrator(
        max_results=3,
        provider_total_seconds=0.08,
        global_budget_seconds=0.40,
        fallback_delay_seconds=0.0,
        cache_ttl_seconds=30,
    )
    private_query = "Python asyncio TaskGroup documentación oficial"
    private_snippet = "PRIVATE-SNIPPET-MARKER unrelated gardening content"

    async def unrelated(_query, _timeout, _limit):
        return [
            {"title": "Gardening tips", "url": "https://gardening.example/soil",
             "snippet": private_snippet},
            {"title": "Home cooking", "url": "https://cooking.example/herbs",
             "snippet": "Recipes and herb preparation"},
        ]

    orchestrator.providers["bing"] = unrelated
    orchestrator.providers["google"] = unrelated
    orchestrator.providers["mojeek"] = unrelated
    orchestrator.providers["duckduckgo"] = unrelated
    orchestrator.fallback_providers["yahoo"] = unrelated
    result = asyncio.run(orchestrator.search(private_query, fresh=True))

    assert result["status"] == "NO_RESULTS"
    matches = [
        record.getMessage()
        for record in caplog.records
        if "web_search_rank_rejected_candidates" in record.getMessage()
    ]
    assert matches, "Expected aggregate rank diagnostics for rejected provider candidates"
    assert "anchor_count=3" in matches[-1]
    assert private_query not in matches[-1]
    assert private_snippet not in matches[-1]


def test_mojeek_search_parses_relevant_general_web_results(monkeypatch):
    import tools.web_search as module
    from backend.search.engine import rank_results

    page = """
    <html><body>
      <ul class="results-standard">
        <li>
          <a class="ob" href="https://docs.python.org/3/library/asyncio.html">docs.python.org</a>
          <h2><a href="https://docs.python.org/3/library/asyncio.html">asyncio - Asynchronous I/O</a></h2>
          <p class="s">Python asyncio documentation and examples.</p>
        </li>
        <li>
          <a class="ob" href="https://realpython.com/async-io-python/">realpython.com</a>
          <h2><a href="https://realpython.com/async-io-python/">Async IO in Python</a></h2>
          <p class="s">Python asyncio and asynchronous programming.</p>
        </li>
        <li>
          <a class="ob" href="https://papajohns.cl/menu">papajohns.cl</a>
          <h2><a href="https://papajohns.cl/menu">Papa Johns menu</a></h2>
          <p class="s">Pizza delivery and family meals.</p>
        </li>
      </ul>
    </body></html>
    """
    monkeypatch.setattr(module, "validate_public_url", lambda value: value)
    module._SEARCH_HTTP_CLIENT = None
    module._SEARCH_HTTP_LOOP = None
    requested = []

    class Client(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            async def handler(request):
                requested.append(str(request.url))
                return httpx.Response(200, request=request, text=page, headers={"content-type": "text/html"})

            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(module.httpx, "AsyncClient", Client)

    async def run():
        result = await module._mojeek_search(
            "Python asyncio official documentation",
            timeout_seconds=4,
            max_results=5,
        )
        await module.close_search_http_client()
        return result

    results = asyncio.run(run())
    ranked = rank_results("Python asyncio official documentation", results)

    assert requested and requested[0].startswith("https://www.mojeek.com/search?")
    assert len(ranked) == 2
    assert all("pizza" not in item["title"].lower() for item in ranked)


def test_bing_rss_unwraps_tracking_urls_before_topic_ranking(monkeypatch):
    import tools.web_search as module
    from backend.search.engine import rank_results

    target = "https://docs.python.org/3/library/asyncio.html"
    wrapped_value = "a1" + base64.urlsafe_b64encode(target.encode()).decode().rstrip("=")
    tracking_url = f"https://www.bing.com/ck/a?u={wrapped_value}"
    xml = (
        "<?xml version=\"1.0\"?><rss><channel>"
        "<item><title>asyncio - Asynchronous I/O</title>"
        f"<link>{tracking_url}</link>"
        "<description>Python asyncio documentation and examples.</description></item>"
        "<item><title>Async IO in Python</title>"
        "<link>https://realpython.com/async-io-python/</link>"
        "<description>Python asyncio and asynchronous programming.</description></item>"
        "</channel></rss>"
    )
    response = httpx.Response(200, content=xml.encode("utf-8"))
    parsed = asyncio.run(module._parse_bing_rss(response, 5))

    assert parsed[0]["url"] == target
    ranked = rank_results("Python asyncio official documentation", parsed)
    assert len(ranked) == 2
    assert {item["url"] for item in ranked} == {
        target,
        "https://realpython.com/async-io-python/",
    }


def test_google_general_web_search_parses_and_ranks_external_results(monkeypatch):
    import tools.web_search as module

    monkeypatch.setattr("tools.web_search.validate_public_url", lambda value: value)

    html_page = """
    <html><body>
      <a href="https://www.google.com/url?q=https%3A%2F%2Fdocs.python.org%2F3%2Flibrary%2Fasyncio-task.html&sa=U">
        <h3>asyncio TaskGroup — Python documentation</h3>
      </a>
      <a href="https://realpython.com/async-io-python/">
        <h3>Python asyncio concurrency guide</h3>
      </a>
      <a href="https://peps.python.org/pep-0654/">
        <h3>Python asyncio TaskGroup exception handling</h3>
      </a>
    </body></html>
    """.encode("utf-8")

    class Client(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            async def handler(request):
                assert request.url.host == "www.google.com"
                assert request.url.path == "/search"
                assert request.url.params["q"] == "Python asyncio TaskGroup official documentation"
                assert request.url.params["hl"] == "en"
                assert request.url.params["gl"] == "US"
                return httpx.Response(200, request=request, content=html_page)

            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    async def client():
        return Client()

    monkeypatch.setattr(module, "_search_http_client", client)
    results = asyncio.run(
        module._google_search("Python asyncio TaskGroup official documentation", 2.5, 5)
    )
    assert len(results) == 3
    assert {item["source_domain"] for item in results} == {
        "docs.python.org",
        "realpython.com",
        "peps.python.org",
    }
    assert all(item["title"] for item in results)


def test_google_result_url_unwraps_only_external_destinations():
    from tools.web_search import _google_result_url

    assert _google_result_url(
        "https://www.google.com/url?q=https%3A%2F%2Fdocs.python.org%2F3%2Flibrary%2Fasyncio.html"
    ) == "https://docs.python.org/3/library/asyncio.html"
    assert _google_result_url("https://www.google.com/search?q=python") == ""
    assert _google_result_url("javascript:alert(1)") == ""


def test_bing_market_params_follow_query_language():
    import tools.web_search as module

    assert module._bing_market_params("python asyncio taskgroup official") == {
        "setlang": "en",
        "cc": "us",
    }
    assert module._bing_market_params("últimas noticias económicas de Chile") == {
        "setlang": "es",
        "cc": "cl",
    }
    assert module._bing_market_params("presidente actual de Chile") == {
        "setlang": "es",
        "cc": "cl",
    }


def test_bing_discards_off_topic_rss_when_html_is_also_off_topic(monkeypatch):
    import tools.web_search as module

    monkeypatch.setattr("tools.web_search.validate_public_url", lambda value: value)
    requested = []

    class Client(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            async def handler(request):
                requested.append(request.url)
                if request.url.params.get("format") == "rss":
                    body = (
                        b'<?xml version="1.0"?><rss><channel>'
                        b"<item><title>Gardening basics</title>"
                        b"<link>https://gardening.example/soil</link>"
                        b"<description>Plant herbs and care for soil.</description></item>"
                        b"</channel></rss>"
                    )
                    return httpx.Response(200, request=request, content=body)
                body = (
                    b'<html><body><li class="b_algo"><h2>'
                    b'<a href="https://cooking.example/herbs">Cooking basics</a>'
                    b"</h2><div class=\"b_caption\"><p>Herbs and recipes.</p>"
                    b"</div></li></body></html>"
                )
                return httpx.Response(200, request=request, content=body)

            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(module.httpx, "AsyncClient", Client)
    results = asyncio.run(module._bing_search("Python asyncio TaskGroup", 2.5, 5))
    assert results == []
    assert len(requested) == 2
    assert requested[0].params["setlang"] == "en"
    assert requested[0].params["cc"] == "us"
    assert requested[1].params["setlang"] == "en"
    assert requested[1].params["cc"] == "us"


def test_bing_rss_failure_does_not_leak_off_topic_html_results(monkeypatch):
    import tools.web_search as module

    monkeypatch.setattr("tools.web_search.validate_public_url", lambda value: value)

    class Client(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            async def handler(request):
                if request.url.params.get("format") == "rss":
                    return httpx.Response(502, request=request)
                body = (
                    b'<html><body><li class="b_algo"><h2>'
                    b'<a href="https://gardening.example/soil">Gardening basics</a>'
                    b"</h2><div class=\"b_caption\"><p>Plant herbs and care for soil.</p>"
                    b"</div></li></body></html>"
                )
                return httpx.Response(200, request=request, content=body)

            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(module.httpx, "AsyncClient", Client)
    results = asyncio.run(module._bing_search("Python asyncio TaskGroup", 2.5, 5))
    assert results == []


def test_bing_falls_back_to_rss_after_http_error(monkeypatch):
    monkeypatch.setenv("WEB_SEARCH_BING_ENABLED", "true")
    monkeypatch.setenv("WEB_SEARCH_MOJEEK_ENABLED", "false")
    monkeypatch.setenv("WEB_SEARCH_FALLBACK_DDG", "false")
    monkeypatch.setattr("tools.web_search.validate_public_url", lambda value: value)

    class Client(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            async def handler(request):
                if request.url.host == "www.bing.com" and request.url.params.get("format") == "rss":
                    return httpx.Response(
                        200,
                        request=request,
                        content=b"""<?xml version="1.0"?><rss><channel><item><title>DEEP33</title><link>https://example.org/deep33</link><description>Search result</description></item></channel></rss>""",
                    )
                if request.url.host == "www.google.com":
                    return httpx.Response(
                        200,
                        request=request,
                        text='<html><body><a href="https://independent.example/deep33"><h3>DEEP33 independent reference</h3></a></body></html>',
                    )
                return httpx.Response(502, request=request)
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("tools.web_search.httpx.AsyncClient", Client)
    result = asyncio.run(search_web("DEEP33", provider="bing", api_key="", fresh=True))
    assert result["ok"] is True
    assert "bing" in result["providers"]
    assert any(item["url"] == "https://example.org/deep33" for item in result["results"])


def test_bing_uses_rss_when_generic_html_results_are_off_topic(monkeypatch):
    import tools.web_search as module

    monkeypatch.setenv("WEB_SEARCH_BING_ENABLED", "true")
    monkeypatch.setenv("WEB_SEARCH_FALLBACK_DDG", "false")
    monkeypatch.setenv("WEB_SEARCH_PROVIDER", "bing")
    monkeypatch.setenv("WEB_SEARCH_MOJEEK_ENABLED", "false")
    monkeypatch.setenv("WEB_SEARCH_MAX_QUERIES", "1")
    monkeypatch.setattr("tools.web_search.validate_public_url", lambda value: value)
    module._SEARCH_CACHE.clear()
    module._SEARCH_INFLIGHT.clear()
    module._SEARCH_HTTP_CLIENT = None
    module._SEARCH_HTTP_LOOP = None
    requested = []

    class Client(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            async def handler(request):
                requested.append(str(request.url))
                if request.url.host == "www.google.com":
                    return httpx.Response(
                        200,
                        request=request,
                        text='<html><body><a href="https://peps.python.org/pep-3156/"><h3>Python asyncio PEP 3156</h3></a></body></html>',
                    )
                if request.url.host == "www.bing.com" and request.url.params.get("format") == "rss":
                    return httpx.Response(
                        200,
                        request=request,
                        headers={"content-type": "application/rss+xml"},
                        content=(
                            b"<?xml version=\"1.0\"?><rss><channel>"
                            b"<item><title>asyncio - Asynchronous I/O</title>"
                            b"<link>https://docs.python.org/3/library/asyncio.html</link>"
                            b"<description>Python asyncio documentation and examples.</description></item>"
                            b"<item><title>Async IO in Python</title>"
                            b"<link>https://realpython.com/async-io-python/</link>"
                            b"<description>Python asyncio and asynchronous programming.</description></item>"
                            b"</channel></rss>"
                        ),
                    )
                return httpx.Response(
                    200,
                    request=request,
                    headers={"content-type": "text/html"},
                    content=(
                        b"<html><body><li class=\"b_algo\"><h2>"
                        b"<a href=\"https://papajohns.cl/menu\">Papa Johns menu</a>"
                        b"</h2><div class=\"b_caption\"><p>Pizza delivery and family meals.</p>"
                        b"</div></li></body></html>"
                    ),
                )

            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(module.httpx, "AsyncClient", Client)
    result = asyncio.run(
        module.search_web(
            "Python asyncio official documentation",
            provider="bing",
            api_key="",
            timeout_seconds=5,
            fresh=True,
        )
    )

    assert result["ok"] is True
    assert any("format=rss" in url for url in requested)
    assert {item["url"] for item in result["results"]} >= {
        "https://docs.python.org/3/library/asyncio.html",
        "https://realpython.com/async-io-python",
    }
    assert all("pizza" not in item["title"].lower() for item in result["results"])


def test_bing_realtime_news_merges_rss_when_html_results_are_irrelevant(monkeypatch):
    import tools.web_search as module

    monkeypatch.setenv("WEB_SEARCH_BING_ENABLED", "true")
    monkeypatch.setenv("WEB_SEARCH_FALLBACK_DDG", "false")
    monkeypatch.setenv("WEB_SEARCH_PROVIDER", "bing")
    monkeypatch.setenv("WEB_SEARCH_MAX_QUERIES", "1")
    monkeypatch.setattr("tools.web_search.validate_public_url", lambda value: value)
    module._SEARCH_CACHE.clear()
    module._SEARCH_INFLIGHT.clear()
    module._SEARCH_HTTP_CLIENT = None
    module._SEARCH_HTTP_LOOP = None
    requested = []

    class Client(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            async def handler(request):
                requested.append(str(request.url))
                if request.url.host == "www.mojeek.com":
                    return httpx.Response(
                        200,
                        request=request,
                        headers={"content-type": "text/html"},
                        content=(
                            b'<html><body><ul class="results-standard"><li><h2>'
                            b'<a class="ob" href="https://news-independent.example/las-condes/actualizacion">'
                            b'Las Condes otra actualizacion local publicada hoy</a>'
                            b'</h2><p class="s">Situacion actual de Las Condes en Chile; otra fuente independiente de hoy.</p>'
                            b"</li></ul></body></html>"
                        ),
                    )
                if request.url.host == "www.bing.com" and request.url.params.get("format") == "rss":
                    return httpx.Response(
                        200,
                        request=request,
                        headers={"content-type": "application/rss+xml"},
                        content=(
                            b"<?xml version=\"1.0\"?><rss><channel><item>"
                            b"<title>Las Condes: alertas locales publicadas hoy</title>"
                            b"<link>https://news.example/las-condes/alertas-hoy</link>"
                            b"<description>Situaci\xc3\xb3n actual de Las Condes en Chile; "
                            b"informaci\xc3\xb3n municipal de hoy.</description>"
                            b"</item></channel></rss>"
                        ),
                    )
                return httpx.Response(
                    200,
                    request=request,
                    headers={"content-type": "text/html"},
                    content=(
                        b"<html><body><li class=\"b_algo\"><h2>"
                        b"<a href=\"https://tech.example/windows\">Windows update</a>"
                        b"</h2><div class=\"b_caption\"><p>Operating system update.</p>"
                        b"</div></li></body></html>"
                    ),
                )

            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(module.httpx, "AsyncClient", Client)
    result = asyncio.run(
        module.search_web(
            "situación actual de la comuna de Las Condes en Chile",
            provider="bing",
            api_key="",
            timeout_seconds=3,
            fresh=True,
        )
    )

    assert result["ok"] is True
    assert any("format=rss" in url for url in requested)
    assert result["results"][0]["url"] == "https://news.example/las-condes/alertas-hoy"
    assert result["verification"]["distinct_providers"] >= 2
    assert {"bing", "mojeek"}.issubset(set(result["providers"]))



def test_redirect_to_private_is_blocked(monkeypatch):
    import tools.web_fetch as module
    monkeypatch.setattr(
        module.socket,
        "getaddrinfo",
        lambda *args,**kwargs: [(2,1,6,"",("93.184.216.34",443))],
    )
    class Client(httpx.AsyncClient):
        def __init__(self,*args,**kwargs):
            kwargs["transport"]=httpx.MockTransport(
                lambda request:httpx.Response(
                    302,request=request,
                    headers={"location":"http://127.0.0.1/secret"},
                )
            )
            super().__init__(*args,**kwargs)
    monkeypatch.setattr(module.httpx,"AsyncClient",Client)
    with pytest.raises(SSRFBlockedError):
        asyncio.run(module.fetch_page("https://example.com/"))


def test_web_status_endpoint(monkeypatch):
    from fastapi.testclient import TestClient

    response = TestClient(__import__("backend.main", fromlist=["app"]).app).get("/v1/web/status")
    assert response.status_code == 200
    body = response.json()
    assert body["tool_loop_enabled"] is True


def test_concurrent_fresh_requests_are_bounded_by_orchestrator(monkeypatch):
    import backend.search.resilient as resilient_module
    import tools.web_search as module

    calls = {"count": 0}

    class FakeOrchestrator:
        async def search(self, query, *, fresh=False):
            calls["count"] += 1
            await asyncio.sleep(0.01)
            return {
                "ok": True,
                "results": [
                    {"title": "A", "url": "https://coalesce.example/a", "snippet": "A"},
                    {"title": "B", "url": "https://coalesce.example/b", "snippet": "B"},
                ],
                "sources": [],
                "providers": ["test"],
                "provider": "test",
                "errors": [],
            }

    fake = FakeOrchestrator()
    monkeypatch.setattr(resilient_module, "get_web_orchestrator", lambda max_results=5: fake)

    async def exercise():
        return await asyncio.gather(
            module.search_web("misma consulta", provider="auto", fresh=True),
            module.search_web("misma consulta", provider="auto", fresh=True),
        )

    first, second = asyncio.run(exercise())
    assert calls["count"] == 2
    assert first["results"] == second["results"]


def test_fetch_coalesces_concurrent_fresh_requests(monkeypatch):
    import tools.web_fetch as module

    calls = {"count": 0}

    monkeypatch.setattr(module, "validate_public_url", lambda value: value)

    async def fake_fetch(**kwargs):
        calls["count"] += 1
        await asyncio.sleep(0.05)
        return {
            "ok": True,
            "url": kwargs["original_url"],
            "final_url": kwargs["current_url"],
            "title": "Example",
            "text": "Contenido real de una página de ejemplo con hechos, contexto y detalles suficientes para validar que el lector devuelve texto utilizable.",
            "content_type": "text/html",
            "bytes": 10,
            "redirects": 0,
            "retrieved_at": "now",
        }

    monkeypatch.setattr(module, "_fetch_page_uncached", fake_fetch)
    module._FETCH_CACHE.clear()
    module._FETCH_INFLIGHT.clear()

    async def exercise():
        return await asyncio.gather(
            module.fetch_page("https://example.com/a", fresh=True),
            module.fetch_page("https://example.com/a", fresh=True),
        )

    first, second = asyncio.run(exercise())
    assert calls["count"] == 1
    assert first["text"] == second["text"]


def test_fetch_http_client_has_connection_pool(monkeypatch):
    import tools.web_fetch as module

    module._FETCH_HTTP_CLIENT = None
    module._FETCH_HTTP_LOOP = None

    async def exercise():
        client = await module._fetch_http_client()
        pool = client._transport._pool
        assert pool._max_connections == 20
        assert pool._max_keepalive_connections == 10
        await client.aclose()
        module._FETCH_HTTP_CLIENT = None
        module._FETCH_HTTP_LOOP = None

    asyncio.run(exercise())


def test_realtime_rank_results_reject_social_media_reposts() -> None:
    from backend.search.engine import rank_results

    results = [
        {
            "title": "Chile: latest events and updates - Facebook",
            "url": "https://news.google.com/rss/articles/social-repost",
            "snippet": "Current news and updates from Chile",
            "published_at": datetime.now(timezone.utc).isoformat(),
        },
        {
            "title": "Chile: latest news from ADN Radio",
            "url": "https://adnradio.cl/noticias/chile",
            "snippet": "Current events in Chile today",
            "published_at": datetime.now(timezone.utc).isoformat(),
        },
    ]

    ranked = rank_results("últimas noticias de Chile hoy", results, realtime=True)

    assert all("facebook" not in item["title"].lower() for item in ranked)
    assert any("ADN Radio" in item["title"] for item in ranked)


def test_long_search_instructions_are_compacted_before_provider_query_limit(monkeypatch):
    import backend.search.resilient as resilient_module
    import tools.web_search as module

    captured = {}

    class FakeOrchestrator:
        async def search(self, query, *, fresh=False):
            captured["query"] = query
            return {
                "ok": True,
                "results": [{
                    "title": "Actualidad verificada",
                    "url": "https://example.com/news",
                    "snippet": "Resultado de prueba",
                }],
                "sources": [],
                "providers": ["test"],
                "providers_attempted": ["test"],
                "provider": "test",
                "errors": [],
                "queries": [query],
                "retrieved_at": "2026-10-10T00:00:00Z",
                "verification": {"relevant_results": 1},
            }

    monkeypatch.setattr(resilient_module, "get_web_orchestrator", lambda max_results=5: FakeOrchestrator())

    long_prompt = (
        "Busca en Internet una noticia real publicada hoy, 10 de octubre de 2026. "
        "Devuelve el título, la fecha de publicación, un resumen y el enlace. "
        "Comprueba que la página existe. Si la búsqueda falla, indica el error. "
        "No inventes noticias ni resultados. "
        + ("Responde en un único mensaje y no inventes información. " * 80)
    )
    assert len(long_prompt) > module.MAX_QUERY_CHARS

    result = asyncio.run(
        module.search_web(long_prompt, provider="auto", fresh=True, fast=True)
    )

    assert result["ok"] is True
    assert len(captured["query"]) <= module.MAX_QUERY_CHARS
    assert "no inventes" not in captured["query"].lower()
    assert "noticia real publicada hoy" in captured["query"].lower()



def test_fetch_bounds_large_html_without_failing_article_navigation(monkeypatch):
    import tools.web_fetch as module

    url = "https://large-article.example/news"
    page = (
        b"<html><head><title>Current news article</title></head>"
        b"<body><article>Relevant article text begins here.</article>"
        + b"padding " * 100
        + b"</body></html>"
    )
    monkeypatch.setattr(module, "validate_public_url", lambda value: value)
    monkeypatch.setattr(module, "_max_response_bytes", lambda: 192)
    module._FETCH_CACHE.clear()
    module._FETCH_INFLIGHT.clear()
    module._FETCH_HTTP_CLIENT = None
    module._FETCH_HTTP_LOOP = None

    class Client(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            async def handler(request):
                return httpx.Response(
                    200,
                    request=request,
                    headers={
                        "content-type": "text/html; charset=utf-8",
                        "content-length": str(len(page)),
                    },
                    content=page,
                )

            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(module.httpx, "AsyncClient", Client)

    result = asyncio.run(module.fetch_page(url, fresh=True, max_text_chars=1000))

    assert result["ok"] is True
    assert result["title"] == "Current news article"
    assert "Relevant article text begins here." in result["text"]
    assert result["bytes"] <= 192

    async def close():
        await module.close_fetch_http_client()

    asyncio.run(close())


def test_web_evidence_falls_back_to_independent_sources_after_blocked_pages(monkeypatch):
    import backend.main as main

    query = "investiga el funcionamiento de Python asyncio y concurrencia"
    urls = [
        "https://blocked-one.example/a",
        "https://blocked-two.example/b",
        "https://blocked-two.example/another",
        "https://working-three.example/c",
        "https://working-four.example/d",
        "https://extra-five.example/e",
    ]

    async def fake_search(*_args, **_kwargs):
        results = [
            {"title": f"Result {index}", "url": url, "snippet": "short result snippet"}
            for index, url in enumerate(urls)
        ]
        return {
            "ok": True,
            "engine": "DEEP33 Search Engine",
            "engine_version": "1.4.0",
            "results": results,
            "sources": results,
            "provider": "test",
            "providers_attempted": ["test"],
            "queries": [query],
            "retrieved_at": "2026-10-12T00:00:00Z",
            "verification": {"distinct_domains": 5},
            "fresh_request": True,
        }

    attempted = []

    attempted = []

    async def fake_fetch(url, **_kwargs):
        attempted.append(url)
        if url in urls[:2]:
            raise RuntimeError("WEB_FETCH_HTTP_403")
        label = "READABLE-THREE" if "working-three" in url else "READABLE-FOUR"
        return {
            "ok": True,
            "title": label,
            "url": url,
            "final_url": url,
            "text": (f"{label} independently retrieved article content. " * 5),
        }

    monkeypatch.setattr(main, "search_web", fake_search)
    monkeypatch.setattr(main, "fetch_page", fake_fetch)

    working, sources, evidence_fragments, _ = asyncio.run(
        main.prepare_web_evidence(
            [{"role": "user", "content": query}],
            request_id="web-fetch-fallback-test",
            deep=True,
        )
    )

    assert working
    # Source links must identify pages that supplied readable text, not blocked pages.
    assert {item["url"] for item in sources} == {urls[3], urls[4], urls[5]}
    assert not any(item["url"] in set(urls[:3]) for item in sources)
    assert set(attempted) == {urls[0], urls[1], urls[3], urls[4], urls[5]}
    assert any("READABLE-THREE" in fragment for fragment in evidence_fragments)
    assert any("READABLE-FOUR" in fragment for fragment in evidence_fragments)


def test_realtime_news_fails_closed_when_publishers_block(monkeypatch):
    import backend.main as main

    query = "¿Cuáles son las noticias más importantes de Chile hoy? Busca en Internet y revisa varias fuentes."
    results = [
        {
            "title": "Chile actualidad uno",
            "url": "https://fuente-uno.example/chile/actualidad-uno",
            "snippet": "Actualización verificable sobre Chile hoy con contexto suficiente para una síntesis degradada. " * 2,
            "provider": "bing",
            "published_at": datetime.now(timezone.utc).isoformat(),
        },
        {
            "title": "Chile actualidad dos",
            "url": "https://fuente-dos.example/noticias/chile-dos",
            "snippet": "Segunda actualización independiente sobre Chile hoy con detalles actuales y verificables. " * 2,
            "provider": "google_news",
            "published_at": datetime.now(timezone.utc).isoformat(),
        },
        {
            "title": "Chile actualidad tres",
            "url": "https://fuente-tres.example/politica/chile-tres",
            "snippet": "Tercera fuente independiente con información reciente de Chile para corroborar los hechos. " * 2,
            "provider": "bing",
            "published_at": datetime.now(timezone.utc).isoformat(),
        },
    ]

    async def fake_search(*_args, **_kwargs):
        return {
            "ok": True,
            "status": "success",
            "results": results,
            "sources": results,
            "provider": "multi",
            "winner_provider": "multi",
            "providers_attempted": ["bing", "google_news"],
            "queries": [query],
            "retrieved_at": "2026-10-18T08:03:00Z",
            "verification": {"distinct_domains": 3, "distinct_providers": 2},
            "fresh_request": True,
        }

    async def blocked_fetch(*_args, **_kwargs):
        raise RuntimeError("WEB_FETCH_HTTP_403")

    monkeypatch.setattr(main, "search_web", fake_search)
    monkeypatch.setattr(main, "fetch_page", blocked_fetch)

    working, sources, evidence_fragments, compact = asyncio.run(
        main.prepare_web_evidence(
            [{"role": "user", "content": query}],
            request_id="realtime-snippet-fallback",
            deep=True,
        )
    )

    assert working is None
    assert sources == []
    assert evidence_fragments == []
    assert compact == []


def test_edge_realtime_search_preserves_multidomain_snippets_when_page_readers_block() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    for function_name in ("deep33-proxy", "deep33-tertiary"):
        source = (
            root / "supabase" / "functions" / function_name / "index.ts"
        ).read_text(encoding="utf-8")
        assert "snippet_fallback: true" in source
        assert "snippetFallback.length < requiredProviders" in source
        assert "snippetDomains.size < requiredProviders" in source
        assert "snippetProviders.length < requiredProviders" in source
        assert "item.snippet.length >= 80" in source
        assert 'status: "degraded"' in source
        assert 'fallbackPath + "-snippet-fallback"' in source


def test_edge_web_navigation_requires_multiple_readable_independent_sources() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    for function_name in ("deep33-proxy", "deep33-tertiary"):
        source = (
            root / "supabase" / "functions" / function_name / "index.ts"
        ).read_text(encoding="utf-8")
        assert "const MAX_WEB_SEARCH_RESULTS = 12;" in source
        assert "const TARGET_READABLE_WEB_SOURCES = 4;" in source
        assert "const MIN_READABLE_WEB_SOURCES = 2;" in source
        assert "pageByUrl.size < TARGET_READABLE_WEB_SOURCES" in source
        assert "const fetchedDomains = new Set<string>();" in source
        assert "const requiredReadableSources = requestedReadableSourceCount(searchQuery)" in source
        assert "readableSources.length < requiredReadableSources" in source
        assert "sources.length < requiredReadableSources" in source
        assert "readableProviderNames.length < requiredProviderCount" in source
        assert "readableDomains.size < requiredProviderCount" in source
        assert "WEB_SEARCH_PROVIDER_QUORUM_NOT_MET" in source
        expected_recovery = "vercel-backend-recovery"
        assert f'fallback_path: "{expected_recovery}"' in source
        assert "async function buildNavigatedWebEvidence(" in source

def test_web_search_endpoint_reaches_third_publisher_when_top_two_are_unreadable(monkeypatch):
    import backend.main as main
    from starlette.requests import Request

    query = "política Chile hoy últimas noticias"
    sources = [
        {
            "title": "Últimas noticias de Chile y el mundo hoy - Meganoticias",
            "url": "https://www.meganoticias.cl/lo-ultimo",
            "snippet": "Últimas noticias actualizadas de Chile.",
            "provider": "duckduckgo",
        },
        {
            "title": "Actualidad en Chile - El País",
            "url": "https://elpais.com/chile/actualidad",
            "snippet": "Noticias de política y actualidad chilena.",
            "provider": "duckduckgo",
        },
        {
            "title": "Últimas noticias de política en Chile hoy: Presidente de la DC analiza las elecciones - T13",
            "url": "https://www.t13.cl/noticia/politica/presidente-dc-analiza-elecciones-15-10-2026",
            "snippet": "Artículo periodístico con declaraciones sobre política en Chile hoy; últimas noticias de las elecciones.",
            "provider": "duckduckgo",
            "published_at": datetime.now(timezone.utc).isoformat(),
        },
    ]
    attempted = []

    async def fake_search(*_args, **_kwargs):
        return {
            "ok": True,
            "status": "success",
            "results": sources,
            "sources": sources,
            "provider": "duckduckgo",
            "winner_provider": "duckduckgo",
            "providers_attempted": ["duckduckgo", "bing"],
            "retrieved_at": "2026-10-16T04:00:00Z",
            "queries": [query],
            "errors": [],
            "verification": {"distinct_domains": 3, "distinct_providers": 2},
        }

    async def fake_fetch(url, **_kwargs):
        attempted.append(url)
        if "t13.cl" not in url:
            return {"ok": False, "error": "WEB_FETCH_HTTP_403"}
        return {
            "ok": True,
            "url": url,
            "final_url": url,
            "title": "Presidente de la DC analiza las elecciones - T13",
            "text": (
                "El artículo informa declaraciones y contexto sobre la actualidad "
                "política en Chile, incluyendo las elecciones parlamentarias y "
                "presidenciales. " * 5
            ),
            "retrieved_at": "2026-10-16T04:00:01Z",
            "published_at": datetime.now(timezone.utc).isoformat(),
        }

    async def allow_controls(*_args, **_kwargs):
        return None

    monkeypatch.setattr(main, "DEEP33_WEB_TOOLS_ENABLED", True)
    monkeypatch.setattr(main, "search_web", fake_search)
    monkeypatch.setattr(main, "fetch_page", fake_fetch)
    monkeypatch.setattr(main, "enforce_client_controls", allow_controls)
    monkeypatch.setattr(main, "session_id_from_request", lambda _request: "test-session")
    monkeypatch.setattr(main, "request_id_from_request", lambda _request: "test-third-publisher")

    request = Request({
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "https",
        "path": "/v1/web/search",
        "raw_path": b"/v1/web/search",
        "query_string": b"q=politica",
        "headers": [],
        "server": ("testserver", 443),
        "client": ("127.0.0.1", 12345),
    })
    result = asyncio.run(main.web_search_endpoint(request, q=query))

    assert any("t13.cl" in url for url in attempted)
    assert result["ok"] is False
    assert "REALTIME_NEWS_QUORUM_NOT_MET" in result["errors"]
    assert result["domains_used"] == 1
    assert result["extracted_text_chars"] >= 80
    assert result["fetched_pages"][0]["url"].startswith("https://www.t13.cl/")

def test_web_search_endpoint_recovers_three_publishers_when_initial_news_search_is_degraded(monkeypatch):
    import backend.main as main
    from starlette.requests import Request

    query = "últimas noticias de Chile hoy"
    published_at = datetime.now(timezone.utc).isoformat()
    sources = [
        {
            "title": f"Últimas noticias de Chile hoy: acontecimiento {index}",
            "url": f"https://medio-{index}.example/noticias/chile-actualidad-{index}",
            "snippet": "Últimas noticias de Chile hoy con información nacional reciente y verificable.",
            "provider": provider,
            "providers": [provider],
            "source_domain": f"medio-{index}.example",
            "published_at": published_at,
        }
        for index, provider in ((1, "bing"), (2, "google_news"))
    ]
    rescued = [{
        "title": "Últimas noticias de Chile hoy: nueva actualización nacional",
        "url": "https://tercer-medio.example/noticias/chile-actualidad",
        "snippet": "Últimas noticias de Chile hoy con información nacional reciente y verificable.",
        "provider": "google_news",
        "provider_sources": ["google_news", "bing"],
        "source_domain": "tercer-medio.example",
        "published_at": published_at,
    }]

    async def fake_search(*_args, **_kwargs):
        return {
            "ok": False,
            "status": "failed",
            "errors": ["REALTIME_NEWS_QUORUM_NOT_MET"],
            "results": sources,
            "sources": sources,
            "providers": ["bing", "google_news"],
            "verification": {
                "distinct_domains": 2,
                "distinct_providers": 2,
                "provider_quorum_met": True,
            },
        }

    async def fake_recent_candidates(*_args, **_kwargs):
        return rescued

    async def fake_fetch(source, **_kwargs):
        url = source["url"]
        provider = source.get("provider", "unknown")
        return {
            "title": source["title"],
            "url": url,
            "final_url": url,
            "domain": source["source_domain"],
            "provider": provider,
            "providers": source.get("providers", [provider]),
            "published_at": source["published_at"],
            "text": (
                "Últimas noticias de Chile hoy. Esta noticia describe hechos "
                "nacionales recientes, declaraciones oficiales y contexto verificable. "
            ) * 4,
            "text_chars": 400,
        }

    async def allow_controls(*_args, **_kwargs):
        return None

    monkeypatch.setattr(main, "DEEP33_WEB_TOOLS_ENABLED", True)
    monkeypatch.setattr(main, "search_web", fake_search)
    monkeypatch.setattr(main, "search_recent_news_candidates", fake_recent_candidates)
    monkeypatch.setattr(main, "_fetch_web_source", fake_fetch)
    monkeypatch.setattr(main, "enforce_client_controls", allow_controls)
    monkeypatch.setattr(main, "session_id_from_request", lambda _request: "test-session")
    monkeypatch.setattr(main, "request_id_from_request", lambda _request: "test-news-recovery")

    request = Request({
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "https",
        "path": "/v1/web/search",
        "raw_path": b"/v1/web/search",
        "query_string": b"q=noticias",
        "headers": [],
        "server": ("testserver", 443),
        "client": ("127.0.0.1", 12345),
    })
    result = asyncio.run(main.web_search_endpoint(request, q=query))

    assert result["ok"] is True
    assert result["status"] == "success"
    assert len(result["results"]) == 3
    assert result["domains_used"] == 3
    assert {
        item["source_domain"] for item in result["results"]
    } == {"medio-1.example", "medio-2.example", "tercer-medio.example"}
    assert all(item["evidence_type"] == "verified_current_news_article" for item in result["results"])


def test_web_search_endpoint_has_bounded_multi_candidate_fetch_fallback():
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "backend/main.py").read_text(encoding="utf-8")
    assert 'max(3, min(8, configured_fetch_candidates))' in source
    assert 'max(3, min(6, configured_fetch_candidates))' in source
    assert 'if strict_news_request else' in source
    assert "if len(fetch_candidates) >= max_fetch_candidates:" in source
    assert "landing_penalty = int(path in landing_paths or len(path_parts) <= 1)" in source



def test_chile_political_news_fans_out_in_primary_wave():
    from backend.search.resilient import WebOrchestrator

    orchestrator = WebOrchestrator(
        max_results=5,
        provider_total_seconds=0.08,
        global_budget_seconds=0.5,
        fallback_delay_seconds=0.0,
        cache_ttl_seconds=30,
    )
    seen = []

    async def provider(query, _timeout, _limit):
        seen.append(query)
        lowered = query.casefold()
        if "site:biobiochile.cl" in lowered:
            return [{
                "title": "Política Chile hoy — BioBioChile",
                "url": "https://www.biobiochile.cl/noticias/nacional/chile/2026/10/18/politica.shtml",
                "snippet": "Noticias de política, gobierno y Congreso en Chile hoy.",
                "source_domain": "biobiochile.cl",
            }]
        if "site:cooperativa.cl" in lowered:
            return [{
                "title": "Actualidad política de Chile — Cooperativa",
                "url": "https://www.cooperativa.cl/noticias/pais/politica/2026/10/18/actualidad/",
                "snippet": "Actualidad política, elecciones y gobierno de Chile hoy.",
                "source_domain": "cooperativa.cl",
            }]
        if "site:latercera.com" in lowered:
            return [{
                "title": "Política y Congreso en Chile — La Tercera",
                "url": "https://www.latercera.com/politica/noticia/chile-politica-hoy/",
                "snippet": "Información política de Chile, Congreso y gobierno hoy.",
                "source_domain": "latercera.com",
            }]
        return []

    for name in list(orchestrator.providers):
        orchestrator.providers[name] = provider
    for name in list(orchestrator.fallback_providers):
        orchestrator.fallback_providers[name] = provider

    result = asyncio.run(
        orchestrator.search("política Chile hoy últimas noticias", fresh=True)
    )

    domains = {item["source_domain"] for item in result["results"]}
    assert result["ok"] is True, result
    assert len(domains) >= 3, result
    assert {"biobiochile.cl", "cooperativa.cl", "latercera.com"} <= domains
    assert any("site:biobiochile.cl" in query.casefold() for query in seen)
    assert any("site:cooperativa.cl" in query.casefold() for query in seen)
    assert any("site:latercera.com" in query.casefold() for query in seen)


def test_academic_ai_research_intent_fans_out_to_independent_domains():
    from backend.search.resilient import WebOrchestrator

    orchestrator = WebOrchestrator(
        max_results=5,
        provider_total_seconds=0.15,
        global_budget_seconds=1.0,
        fallback_delay_seconds=0.0,
        cache_ttl_seconds=30,
    )

    async def provider(query, _timeout, _limit):
        lowered = query.casefold()
        if "site:arxiv.org" in lowered:
            return [{
                "title": "Universidad investigación inteligencia artificial paper — arXiv",
                "url": "https://arxiv.org/abs/2601.00001",
                "snippet": "Academic artificial intelligence research paper from university researchers.",
            }]
        if "site:ai.stanford.edu" in lowered:
            return [{
                "title": "Stanford universidad investigación inteligencia artificial paper",
                "url": "https://ai.stanford.edu/research/",
                "snippet": "University artificial intelligence research publications and papers.",
            }]
        if "site:csail.mit.edu" in lowered:
            return [{
                "title": "MIT universidad investigación inteligencia artificial paper",
                "url": "https://www.csail.mit.edu/research/artificial-intelligence",
                "snippet": "Academic AI research papers from a university laboratory.",
            }]
        return [{
            "title": "UCLA universidad investigación inteligencia artificial",
            "url": "https://www.ucla.edu/research/artificial-intelligence",
            "snippet": "University artificial intelligence research.",
        }]

    for name in list(orchestrator.providers):
        orchestrator.providers[name] = provider
    for name in list(orchestrator.fallback_providers):
        orchestrator.fallback_providers[name] = provider

    result = asyncio.run(
        orchestrator.search(
            "universidad investigación inteligencia artificial paper",
            fresh=True,
        )
    )

    domains = {item["source_domain"] for item in result["results"]}
    assert result["ok"] is True, result
    assert len(domains) >= 3, result
    assert {"arxiv.org", "ai.stanford.edu", "csail.mit.edu"} & domains


def test_stream_gateway_emits_visible_fallback_when_provider_finishes_empty(monkeypatch):
    import backend.main as main

    async def fake_stream(payload, **kwargs):
        yield b"data: [DONE]\n\n"

    monkeypatch.setattr(main.gateway, "stream", fake_stream)

    async def fake_complete(_payload, **_kwargs):
        return {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "Respuesta recuperada mediante completion normal.",
                },
                "finish_reason": "stop",
            }],
            "_deep33_gateway": {"provider": "test", "model": "test"},
        }

    monkeypatch.setattr(main.gateway, "complete", fake_complete)

    async def exercise():
        completion_state = {}
        generator = main.stream_gateway(
            {"messages": [{"role": "user", "content": "busca en internet"}], "model": "test"},
            "session",
            "NEUTRO",
            request_id="empty-visible-fallback",
            idempotency_key="empty-visible-fallback",
            request_hash="empty-visible-fallback",
            lease_token="lease",
            completion_state=completion_state,
        )
        output = bytearray()
        async for part in generator:
            output.extend(part)
        raw = output.decode("utf-8")
        assert "AI_GATEWAY_INVALID_RESPONSE" not in raw
        assert '"content":' in raw
        assert "Respuesta recuperada mediante completion normal." in raw
        assert "No pude completar la síntesis" not in raw
        assert raw.endswith("data: [DONE]\n\n")
        assert completion_state["assistant_text"] == "Respuesta recuperada mediante completion normal."

    asyncio.run(exercise())


def test_fetch_web_source_preserves_google_news_and_resolver_providers(monkeypatch):
    import asyncio
    import backend.main as main

    wrapper = "https://news.google.com/rss/articles/opaque-id?oc=5"
    publisher_url = "https://www.theclinic.cl/2026/11/24/noticia-hualpen-pdi/"
    source = {
        "provider": "google_news",
        "providers": ["google_news", "mwmbl"],
        "source_domain": "theclinic.cl",
        "title": "Hualpén: procedimiento de la PDI",
        "url": wrapper,
    }

    async def fake_search(*_args, **_kwargs):
        return {
            "results": [{
                "url": publisher_url,
                "title": "Hualpén: procedimiento de la PDI",
                "provider": "bing",
            }]
        }

    async def fake_fetch(url, **_kwargs):
        assert url == publisher_url
        return {
            "ok": True,
            "url": url,
            "final_url": url,
            "title": "Hualpén: procedimiento de la PDI",
            "text": ("La PDI realizó un procedimiento en Hualpén, Región del Biobío. " * 8),
            "published_at": datetime.now(timezone.utc).isoformat(),
        }

    monkeypatch.setattr(main, "search_web", fake_search)
    monkeypatch.setattr(main, "fetch_page", fake_fetch)

    page = asyncio.run(
        main._fetch_web_source(
            source,
            request_id="test-provider-provenance",
            timeout_seconds=2.0,
        )
    )

    assert page is not None
    assert page["provider"] == "bing"
    assert page["providers"] == ["bing", "google_news", "mwmbl"]


def test_recent_news_quorum_uses_provider_field_when_provider_list_is_empty():
    import backend.main as main

    query = "últimas noticias de Chile hoy"
    published_at = datetime.now(timezone.utc).isoformat()
    pages = [
        {
            "provider": "google_news",
            "providers": [],
            "domain": "theclinic.cl",
            "final_url": "https://www.theclinic.cl/2026/11/24/noticia-hualpen/",
            "title": "Hualpén: procedimiento de la PDI",
            "published_at": published_at,
            "text": ("La PDI realizó un procedimiento en Hualpén, Región del Biobío. " * 8),
        },
        {
            "provider": "mwmbl",
            "providers": [],
            "domain": "cooperativa.cl",
            "final_url": "https://www.cooperativa.cl/noticias/pais/2026/11/24/noticia-san-miguel/",
            "title": "Municipalidad de San Miguel anuncia medidas",
            "published_at": published_at,
            "text": ("La Municipalidad de San Miguel informó nuevas medidas para Chile. " * 8),
        },
        {
            "provider": "publisher_rss",
            "providers": [],
            "domain": "latercera.com",
            "final_url": "https://www.latercera.com/politica/noticia/diputados/",
            "title": "Diputados revisan presupuesto en el Congreso",
            "published_at": published_at,
            "text": ("Diputados revisaron el presupuesto en el Congreso de Chile. " * 8),
        },
    ]
    search_result = {
        "providers": ["bing", "duckduckgo"],
        "verification": {
            "distinct_providers": 1,
            "provider_quorum_met": False,
        },
    }
    main._merge_recovered_provider_provenance(search_result, pages)

    summary = main._recent_news_quorum_summary(query, pages, search_result)

    assert search_result["verification"]["distinct_providers"] == 3
    assert search_result["verification"]["provider_quorum_met"] is True
    assert summary["articles"] == 3
    assert summary["domains"] == 3
    assert summary["providers"] == 2
    assert summary["met"] is True
    assert main._independent_search_provider_family("unknown") == ""


def test_recovered_provider_provenance_updates_quorum_without_counting_rss():
    import backend.main as main

    search_result = {
        "providers": ["bing", "duckduckgo"],
        "verification": {
            "distinct_providers": 1,
            "provider_quorum_met": False,
        },
    }
    main._merge_recovered_provider_provenance(
        search_result,
        [{
            "provider": "google_news",
            "provider_sources": ["google_news", "mwmbl"],
        }],
    )
    assert search_result["verification"]["distinct_providers"] == 3
    assert search_result["verification"]["provider_quorum_met"] is True
    assert {"bing", "google_news", "mwmbl"}.issubset(set(search_result["providers"]))

    rss_only = {
        "providers": ["bing", "duckduckgo"],
        "verification": {
            "distinct_providers": 1,
            "provider_quorum_met": False,
        },
    }
    main._merge_recovered_provider_provenance(
        rss_only,
        [{"provider": "publisher_rss", "provider_sources": ["publisher_rss"]}],
    )
    assert rss_only["verification"]["distinct_providers"] == 1
    assert rss_only["verification"]["provider_quorum_met"] is False


def test_candidate_provider_names_falls_back_when_provenance_lists_are_empty():
    import backend.main as main

    assert main._candidate_provider_names(
        {"provider_sources": [], "providers": []},
        "google_news",
    ) == ["google_news"]
    assert main._candidate_provider_names(
        {"provider_sources": [], "providers": ["bing", "google_news"]},
        "unknown",
    ) == ["bing", "google_news"]
    assert main._candidate_provider_names(
        {"provider_sources": ["publisher_rss"]},
        "bing",
    ) == ["publisher_rss"]


def test_broad_chile_news_accepts_fresh_local_articles_not_unrelated_global_items():
    import backend.main as main

    query = "últimas noticias de Chile hoy"
    published_at = datetime.now(timezone.utc).isoformat()
    local_source = {
        "provider": "publisher_rss",
        "source_domain": "theclinic.cl",
        "title": "Contrabando de especies exóticas en Hualpén",
    }
    local_page = {
        "provider": "publisher_rss",
        "domain": "theclinic.cl",
        "final_url": "https://www.theclinic.cl/2026/11/24/noticia-hualpen-pdi/",
        "title": "Contrabando de especies exóticas en Hualpén",
        "published_at": published_at,
        "text": (
            "La PDI detuvo a un hombre en Hualpén por contrabando de especies exóticas. "
            "El procedimiento ocurrió en la Región del Biobío. "
        ) * 8,
    }
    global_source = {
        "provider": "publisher_rss",
        "source_domain": "df.cl",
        "title": "Nvidia actualiza sus planes internacionales",
    }
    global_page = {
        "provider": "publisher_rss",
        "domain": "df.cl",
        "final_url": "https://www.df.cl/internacional/multinacionales/nvidia-planes",
        "title": "Nvidia actualiza sus planes internacionales",
        "published_at": published_at,
        "text": (
            "La compañía anunció nuevas inversiones y cambios en sus operaciones "
            "internacionales. " * 12
        ),
    }

    assert main._is_publishable_realtime_news_source(query, local_source, local_page) is True
    assert main._is_publishable_realtime_news_source(query, global_source, global_page) is False


def test_chat_realtime_news_requires_three_fresh_publishers_and_two_providers(monkeypatch):
    import backend.main as main

    query = "últimas noticias de Chile hoy"
    published_at = datetime.now(timezone.utc).isoformat()
    fixtures = [
        {
            "title": "Últimas noticias de Chile hoy: Congreso aprueba nueva medida",
            "url": "https://www.biobiochile.cl/noticias/2026/11/09/chile-politica-a.shtml",
            "snippet": "Últimas noticias de Chile hoy sobre Congreso, gobierno y actualidad nacional.",
            "provider": "bing",
            "providers": ["bing"],
            "source_domain": "biobiochile.cl",
            "published_at": published_at,
        },
        {
            "title": "Últimas noticias de Chile hoy: autoridades anuncian cambios",
            "url": "https://www.cooperativa.cl/noticias/pais/2026/11/09/chile-actualidad.shtml",
            "snippet": "Noticias de Chile hoy, actualidad nacional y declaraciones de autoridades.",
            "provider": "google_news",
            "providers": ["google_news"],
            "source_domain": "cooperativa.cl",
            "published_at": published_at,
        },
    ]
    recovered_fixture = {
        "title": "Últimas noticias de Chile hoy: nueva información política",
        "url": "https://www.latercera.com/politica/noticia/chile-actualidad/ABC123/",
        "snippet": "Últimas noticias de Chile hoy con información política y nacional.",
        "provider": "mwmbl",
        "provider_sources": ["mwmbl"],
        "source_domain": "latercera.com",
    }

    async def fake_search(_query, **_kwargs):
        return {
            "ok": False,
            "status": "failed",
            "realtime": True,
            "fresh_request": True,
            "providers": ["bing", "google_news"],
            "providers_attempted": ["bing", "google_news"],
            "verification": {
                "provider_quorum_met": True,
                "distinct_providers": 2,
            },
            "sources": fixtures,
            "results": fixtures,
            "queries": [query],
            "retrieved_at": published_at,
            "errors": ["REALTIME_NEWS_QUORUM_NOT_MET"],
        }

    async def fake_fetch(source, **_kwargs):
        provider = source["provider"]
        return {
            "title": source["title"],
            "url": source["url"],
            "final_url": source["url"],
            "text": (
                "Últimas noticias de Chile hoy. "
                "Información periodística actualizada sobre Congreso, autoridades, "
                "gobierno y acontecimientos nacionales. " * 4
            ),
            "provider": provider,
            "providers": source.get("providers", [provider]),
            "domain": source["source_domain"],
            "source_domain": source["source_domain"],
            "published_at": source.get("published_at") or published_at,
            "retrieved_at": published_at,
            "text_chars": 400,
        }

    async def fake_fetch_quorum(candidates, fetcher, *, policy):
        documents = []
        for source in candidates:
            page = await fetcher(source, policy.base_timeout_seconds)
            if page is not None:
                documents.append(page)
        domains = {page["domain"] for page in documents}
        return type("FetchBatch", (), {
            "documents": documents,
            "status": "success" if len(documents) >= 3 else "degraded",
            "fetched": len(documents),
            "distinct_domains": len(domains),
            "total_text_chars": sum(len(page["text"]) for page in documents),
            "cancelled": 0,
            "elapsed_ms": 10.0,
        })()

    monkeypatch.setattr(main, "search_web", fake_search)
    monkeypatch.setattr(main, "_fetch_web_source", fake_fetch)
    monkeypatch.setattr(main, "fetch_source_quorum", fake_fetch_quorum)
    monkeypatch.setattr(
        main,
        "search_recent_news_candidates",
        lambda *args, **kwargs: asyncio.sleep(0, result=[recovered_fixture]),
    )

    async def exercise():
        return await main.prepare_web_evidence(
            [{"role": "user", "content": query}],
            request_id="news-quorum-test",
            deep=False,
        )

    working, source_links, evidence_fragments, compact = asyncio.run(exercise())
    assert working is not None
    assert compact["news_quorum"]["met"] is True
    assert compact["news_quorum"]["articles"] == 3
    assert compact["news_quorum"]["domains"] == 3
    assert compact["news_quorum"]["providers"] >= 2
    assert len(evidence_fragments) == 3
    assert len(source_links) == 3
    assert len(compact["fetched_pages"]) == 3
    assert len(compact["search_results"]) == 3


def test_chat_realtime_news_rejects_three_articles_from_one_provider(monkeypatch):
    import backend.main as main

    query = "últimas noticias de Chile hoy"
    published_at = datetime.now(timezone.utc).isoformat()
    pages = [
        {
            "title": f"Últimas noticias de Chile hoy: acontecimiento nacional {index}",
            "url": f"https://publisher{index}.example/noticias/chile-actualidad-{index}",
            "final_url": f"https://publisher{index}.example/noticias/chile-actualidad-{index}",
            "domain": f"publisher{index}.example",
            "provider": "bing",
            "published_at": published_at,
            "snippet": "Noticias recientes de Chile y actualidad nacional.",
            "text": "Noticias recientes de Chile y actualidad nacional. " * 8,
        }
        for index in range(1, 4)
    ]
    search_result = {
        "verification": {
            "provider_quorum_met": True,
            "distinct_providers": 2,
        }
    }

    summary = main._recent_news_quorum_summary(query, pages, search_result)

    assert summary["articles"] == 3
    assert summary["domains"] == 3
    assert summary["providers"] == 1
    assert summary["provider_quorum_met"] is False
    assert summary["met"] is False



def test_chat_realtime_news_does_not_count_publisher_rss_as_search_provider():
    import backend.main as main

    query = "últimas noticias de Chile hoy"
    published_at = datetime.now(timezone.utc).isoformat()
    pages = [
        {
            "title": "Noticia reciente de Chile desde Bing",
            "url": "https://bing-publisher.example/noticias/chile-bing",
            "final_url": "https://bing-publisher.example/noticias/chile-bing",
            "domain": "bing-publisher.example",
            "provider": "bing",
            "providers": ["bing"],
            "published_at": published_at,
            "text": "Información periodística reciente de Chile. " * 5,
        },
        {
            "title": "Noticia reciente de Chile desde feed editorial",
            "url": "https://feed-publisher.example/noticias/chile-feed",
            "final_url": "https://feed-publisher.example/noticias/chile-feed",
            "domain": "feed-publisher.example",
            "provider": "publisher_rss",
            "providers": ["publisher_rss"],
            "published_at": published_at,
            "text": "Información periodística reciente de Chile. " * 5,
        },
        {
            "title": "Otra noticia reciente de Chile desde feed editorial",
            "url": "https://feed-publisher-two.example/noticias/chile-feed",
            "final_url": "https://feed-publisher-two.example/noticias/chile-feed",
            "domain": "feed-publisher-two.example",
            "provider": "publisher_rss",
            "providers": ["publisher_rss"],
            "published_at": published_at,
            "text": "Información periodística reciente de Chile. " * 5,
        },
    ]
    search_result = {
        "verification": {
            "provider_quorum_met": True,
            "distinct_providers": 2,
        }
    }

    summary = main._recent_news_quorum_summary(query, pages, search_result)

    assert summary["articles"] == 3
    assert summary["domains"] == 3
    assert summary["providers"] == 1
    assert summary["provider_quorum_met"] is False
    assert summary["met"] is False


def test_chat_realtime_news_fails_closed_without_three_publishers(monkeypatch):
    import backend.main as main

    query = "últimas noticias de Chile hoy"
    published_at = "2026-11-09T20:00:00+00:00"
    fixtures = [
        {
            "title": "Últimas noticias de Chile hoy: Congreso aprueba nueva medida",
            "url": "https://www.biobiochile.cl/noticias/2026/11/09/chile-politica-a.shtml",
            "snippet": "Últimas noticias de Chile hoy sobre Congreso y gobierno.",
            "provider": "bing",
            "source_domain": "biobiochile.cl",
            "published_at": published_at,
        },
        {
            "title": "Últimas noticias de Chile hoy: autoridades anuncian cambios",
            "url": "https://www.cooperativa.cl/noticias/pais/2026/11/09/chile-actualidad.shtml",
            "snippet": "Noticias de Chile hoy, actualidad nacional y declaraciones.",
            "provider": "duckduckgo",
            "source_domain": "cooperativa.cl",
            "published_at": published_at,
        },
    ]

    async def fake_search(_query, **_kwargs):
        return {
            "ok": True,
            "realtime": True,
            "fresh_request": True,
            "providers": ["bing", "duckduckgo"],
            "verification": {
                "provider_quorum_met": True,
                "distinct_providers": 2,
            },
            "sources": fixtures,
            "results": fixtures,
            "queries": [query],
            "retrieved_at": published_at,
        }

    async def fake_fetch(source, **_kwargs):
        return {
            "title": source["title"],
            "url": source["url"],
            "text": (
                "Últimas noticias de Chile hoy. "
                "Información periodística actualizada sobre Congreso, autoridades "
                "y acontecimientos nacionales. " * 4
            ),
            "provider": source["provider"],
            "domain": source["source_domain"],
            "source_domain": source["source_domain"],
            "published_at": source["published_at"],
            "retrieved_at": published_at,
            "text_chars": 400,
        }

    async def fake_fetch_quorum(candidates, fetcher, *, policy):
        documents = []
        for source in candidates:
            page = await fetcher(source, policy.base_timeout_seconds)
            if page is not None:
                documents.append(page)
        domains = {page["domain"] for page in documents}
        return type("FetchBatch", (), {
            "documents": documents,
            "status": "degraded",
            "fetched": len(documents),
            "distinct_domains": len(domains),
            "total_text_chars": sum(len(page["text"]) for page in documents),
            "cancelled": 0,
            "elapsed_ms": 10.0,
        })()

    monkeypatch.setattr(main, "search_web", fake_search)
    monkeypatch.setattr(main, "_fetch_web_source", fake_fetch)
    monkeypatch.setattr(main, "fetch_source_quorum", fake_fetch_quorum)
    monkeypatch.setattr(main, "search_recent_news_candidates", lambda *args, **kwargs: asyncio.sleep(0, result=[]))

    async def exercise():
        return await main.prepare_web_evidence(
            [{"role": "user", "content": query}],
            request_id="news-quorum-insufficient-test",
            deep=False,
        )

    evidence, sources, pages, snippets = asyncio.run(exercise())
    assert evidence is None
    assert sources == []
    assert pages == []
    assert snippets == []


def test_chile_current_events_query_gets_broad_news_recovery():
    from backend.search.resilient import WebOrchestrator

    query = "principales acontecimientos actuales en Chile hoy"
    recovery = WebOrchestrator._recovery_query(query).casefold()

    assert recovery != query.casefold()
    assert recovery == "chile noticias hoy actualidad acontecimientos recientes"
    assert "chile" in recovery
    assert "noticias" in recovery
    # General-web recovery stays broad; source restrictions are not baked into it.
    assert "site:" not in recovery


def test_chile_current_events_provider_query_uses_news_vocabulary():
    from backend.search.resilient import WebOrchestrator

    query = "principales acontecimientos actuales en Chile hoy"
    provider_query = WebOrchestrator._provider_query(query)
    recovery_query = WebOrchestrator._recovery_query(query)

    assert provider_query == "Chile noticias hoy actualidad acontecimientos recientes"
    assert recovery_query == provider_query
    assert "site:" not in provider_query


def test_web_search_endpoint_fetches_orchestrator_selected_sources_when_secondary_score_is_empty(monkeypatch):
    import asyncio
    from starlette.requests import Request
    import backend.main as main

    query = "producto tecnológico smartphone actual especificaciones"
    selected = [
        {
            "title": "Phone specifications",
            "url": "https://phones.example/device-a",
            "snippet": "Current mobile device specifications and comparison.",
            "provider": "bing",
            "source_domain": "phones.example",
        },
        {
            "title": "Mobile review",
            "url": "https://reviews.example/device-b",
            "snippet": "New phone model technical specifications.",
            "provider": "bing",
            "source_domain": "reviews.example",
        },
    ]

    async def fake_search_web(*args, **kwargs):
        return {
            "ok": True,
            "status": "OK",
            "engine": "DEEP33 Search Engine",
            "engine_version": "1.4.0",
            "realtime": True,
            "fresh_request": True,
            "retrieved_at": "2026-10-18T08:00:00+00:00",
            "results": selected,
            "sources": selected,
            "providers": ["bing"],
            "providers_attempted": ["bing", "duckduckgo"],
            "verification": {
                "level": "multi-source",
                "distinct_domains": 2,
                "distinct_providers": 1,
                "corroborated_results": 0,
            },
        }

    attempted = []

    async def fake_fetch(source, **kwargs):
        attempted.append(source["url"])
        return {
            "title": source["title"],
            "url": source["url"],
            "domain": source["source_domain"],
            "provider": source["provider"],
            "text": ("Verified extracted phone specification page content. " * 8),
            "text_chars": 400,
            "retrieved_at": "2026-10-18T08:00:01+00:00",
        }

    monkeypatch.setattr(main, "search_web", fake_search_web)
    monkeypatch.setattr(main, "_fetch_web_source", fake_fetch)
    # Force the endpoint-only heuristic below threshold. The orchestrator-selected
    # URLs must still be fetched; they are not exposed unless extraction succeeds.
    monkeypatch.setattr(main, "score_result", lambda *_args, **_kwargs: 0.05)
    monkeypatch.setattr(main, "enforce_client_controls", lambda *_args, **_kwargs: asyncio.sleep(0))

    request = Request({
        "type": "http",
        "method": "GET",
        "path": "/v1/web/search",
        "headers": [(b"x-deep33-session-id", b"test-web-fetch-fallback")],
        "query_string": b"",
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
        "scheme": "http",
    })
    result = asyncio.run(main.web_search_endpoint(request, q=query))

    assert result["ok"] is True
    assert result["status"] == "success"
    assert len(attempted) == 2
    assert set(attempted) == {item["url"] for item in selected}
    assert result["domains_used"] == 2
    assert len(result["results"]) == 2
    assert result["extracted_text_chars"] >= 160

def test_realtime_provider_query_compacts_long_spanish_instructions() -> None:
    from backend.search.resilient import WebOrchestrator

    query = (
        "Busca en Internet cuál es la versión estable más reciente de Python publicada oficialmente. "
        "Contrasta las páginas oficiales recuperadas y cita enlaces directos."
    )
    provider_query = WebOrchestrator._provider_query(query).casefold()

    assert len(provider_query.split()) <= 10
    assert "python" in provider_query
    assert "versión" in provider_query or "version" in provider_query
    assert "official" in provider_query
    assert "contrasta" not in provider_query
    assert "recuperadas" not in provider_query
    assert "enlaces" not in provider_query


def test_realtime_provider_query_preserves_country_when_compacting() -> None:
    from backend.search.resilient import WebOrchestrator

    query = "Busca en Internet el valor actual del dólar en Chile y cita fuentes oficiales."
    provider_query = WebOrchestrator._provider_query(query).casefold()

    assert "chile" in provider_query
    assert "dólar" in provider_query or "dolar" in provider_query
    assert len(provider_query.split()) <= 10



def test_force_web_geocodes_only_explicit_current_weather_queries(monkeypatch):
    import backend.main as main

    calls = []

    async def fake_weather_resolver(*args, **kwargs):
        calls.append(args)
        return None

    async def fake_prepare_web_evidence(*args, **kwargs):
        return None, [], [], []

    monkeypatch.setattr(main, "resolve_gps_weather", fake_weather_resolver)
    monkeypatch.setattr(main, "prepare_web_evidence", fake_prepare_web_evidence)

    async def run(query):
        return await main.run_web_tool_loop(
            [{"role": "user", "content": query}],
            model="kilo-auto/free",
            max_tokens=128,
            request_id="weather-routing-contract",
            idempotency_key="weather-routing-contract",
            force_web=True,
            deadline=None,
            personality=main.DEFAULT_PERSONALITY,
            weather=None,
        )

    asyncio.run(run("Busca el precio internacional del cobre hoy"))
    assert calls == []

    asyncio.run(run("¿Cuál es el clima actual en Santiago?"))
    assert len(calls) == 1


def test_web_orchestrator_ranks_against_compacted_semantic_query(monkeypatch):
    from backend.search import resilient

    query = (
        "Ejecuta una búsqueda web real sobre el precio internacional del cobre hoy. "
        "No uses memoria ni datos almacenados; consulta fuentes independientes."
    )
    expected_query = resilient.WebOrchestrator._provider_query(query)
    ranked_queries = []

    def capture_rank_query(rank_query, results, **kwargs):
        ranked_queries.append(rank_query)
        return list(results or [])

    async def provider(_query, _timeout, _limit):
        return [
            {
                "title": "Precio internacional del cobre hoy",
                "url": "https://market-one.example/copper-price",
                "source_domain": "market-one.example",
                "snippet": "Cotización internacional del cobre y evolución del precio.",
            },
            {
                "title": "Mercado mundial del cobre",
                "url": "https://market-two.example/copper-market",
                "source_domain": "market-two.example",
                "snippet": "Precio del cobre en los mercados internacionales.",
            },
        ]

    monkeypatch.setattr(resilient, "rank_results", capture_rank_query)
    orchestrator = resilient.WebOrchestrator(max_results=8, global_budget_seconds=2.0)
    orchestrator.providers = {"bing": provider, "google": provider}
    orchestrator.fallback_providers = {}

    result = asyncio.run(orchestrator.search(query, fresh=True, budget_seconds=2.0))

    assert result["ok"] is True
    assert ranked_queries
    assert all(value == expected_query for value in ranked_queries)
    assert "memoria" not in expected_query.casefold()
    assert "ejecuta" not in expected_query.casefold()
