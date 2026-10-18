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
        if provider == "bing":
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
    assert len(sources) == len(urls)
    assert set(attempted) == {urls[0], urls[1], urls[3], urls[4], urls[5]}
    assert any("READABLE-THREE" in fragment for fragment in evidence_fragments)
    assert any("READABLE-FOUR" in fragment for fragment in evidence_fragments)


def test_edge_web_navigation_recovers_multiple_sources_but_accepts_one_readable() -> None:
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
        assert "if (!search.ok || sources.length < 1)" in source
        assert 'fallback_path: "railway-recovery"' in source
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
    assert result["ok"] is True
    assert result["domains_used"] == 1
    assert result["extracted_text_chars"] >= 80
    assert result["fetched_pages"][0]["url"].startswith("https://www.t13.cl/")

def test_web_search_endpoint_has_bounded_multi_candidate_fetch_fallback():
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "backend/main.py").read_text(encoding="utf-8")
    assert 'max_fetch_candidates = max(3, min(6, int(os.getenv("WEB_SEARCH_FETCH_CANDIDATES", "5"))))' in source
    assert "if len(fetch_candidates) >= max_fetch_candidates:" in source
    assert "landing_penalty = int(path in landing_paths or len(path_parts) <= 1)" in source



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
        assert "No pude completar la síntesis" in raw
        assert raw.endswith("data: [DONE]\n\n")
        assert completion_state["assistant_text"].startswith("No pude completar la síntesis")

    asyncio.run(exercise())


def test_chile_current_events_query_gets_broad_news_recovery():
    from backend.search.resilient import WebOrchestrator

    query = "principales acontecimientos actuales en Chile hoy"
    recovery = WebOrchestrator._recovery_query(query).casefold()

    assert recovery != query.casefold()
    assert "chile" in recovery
    assert "noticias" in recovery
    assert any(term in recovery for term in ("sociedad", "economía", "seguridad", "ciencia"))
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
