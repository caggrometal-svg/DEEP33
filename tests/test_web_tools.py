from __future__ import annotations
import asyncio
import base64
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
    from backend.search.engine import SearchEngine

    captured = {}

    async def fake_search(self, query, **kwargs):
        captured["query"] = query
        return {
            "ok": True,
            "engine": "DEEP33 Search Engine",
            "engine_version": "1.3.0",
            "results": [{"title": "DEEP33 Example", "url": "https://example.com/", "snippet": "Snippet"}],
            "providers": ["bing"],
            "provider": "bing",
            "errors": [],
        }

    monkeypatch.setattr(SearchEngine, "search", fake_search)
    result = asyncio.run(search_web("  DEEP33  ", provider="auto", fresh=True))
    assert captured["query"] == "DEEP33"
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
    orchestrator.providers["mojeek"] = failing
    orchestrator.providers["duckduckgo"] = ddg
    result = asyncio.run(orchestrator.search("DEEP33 fallback test", fresh=True))
    assert result["winner_provider"] == "duckduckgo"

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


def test_bing_falls_back_to_rss_after_http_error(monkeypatch):
    monkeypatch.setenv("WEB_SEARCH_BING_ENABLED", "true")
    monkeypatch.setenv("WEB_SEARCH_FALLBACK_DDG", "false")
    monkeypatch.setattr("tools.web_search.validate_public_url", lambda value: value)

    class Client(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            async def handler(request):
                if request.url.params.get("format") == "rss":
                    return httpx.Response(
                        200,
                        request=request,
                        content=b"""<?xml version="1.0"?><rss><channel><item><title>DEEP33</title><link>https://example.org/deep33</link><description>Search result</description></item></channel></rss>""",
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
                if request.url.params.get("format") == "rss":
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
                if request.url.params.get("format") == "rss":
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


def test_concurrent_fresh_requests_coalesce_into_one_engine_call(monkeypatch):
    import tools.web_search as module
    from backend.search.engine import SearchEngine

    calls = {"count": 0}

    async def fake_search(self, query, **kwargs):
        calls["count"] += 1
        await asyncio.sleep(0.05)
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

    monkeypatch.setattr(SearchEngine, "search", fake_search)
    module._SEARCH_CACHE.clear()
    module._SEARCH_INFLIGHT.clear()

    async def exercise():
        return await asyncio.gather(
            module.search_web("misma consulta", provider="auto", fresh=True),
            module.search_web("misma consulta", provider="auto", fresh=True),
        )

    first, second = asyncio.run(exercise())
    assert calls["count"] == 1
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
            "text": "contenido",
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
            "published_at": "2026-10-10T05:00:00Z",
        },
        {
            "title": "Chile: latest news from ADN Radio",
            "url": "https://adnradio.cl/noticias/chile",
            "snippet": "Current events in Chile today",
            "published_at": "2026-10-10T05:00:00Z",
        },
    ]

    ranked = rank_results("últimas noticias de Chile hoy", results, realtime=True)

    assert all("facebook" not in item["title"].lower() for item in ranked)
    assert any("ADN Radio" in item["title"] for item in ranked)


def test_long_search_instructions_are_compacted_before_provider_query_limit(monkeypatch):
    import tools.web_search as module
    from backend.search.engine import SearchEngine

    captured = {}

    async def fake_search(self, query, **kwargs):
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

    monkeypatch.setattr(SearchEngine, "search", fake_search)

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


def test_edge_web_navigation_requires_multiple_independent_sources() -> None:
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
        assert "navigation.succeeded < MIN_READABLE_WEB_SOURCES" in source
        assert "async function buildNavigatedWebEvidence(" in source
