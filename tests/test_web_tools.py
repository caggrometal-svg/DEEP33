from __future__ import annotations
import asyncio
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

def test_tavily_normalisation(monkeypatch):
    monkeypatch.setenv("WEB_SEARCH_BING_ENABLED", "false")
    monkeypatch.setenv("WEB_SEARCH_FALLBACK_DDG", "false")
    monkeypatch.setenv("WEB_SEARCH_BING_ENABLED", "false")
    monkeypatch.setenv("WEB_SEARCH_FALLBACK_DDG", "false")
    monkeypatch.setattr("tools.web_search.validate_public_url", lambda value: value)
    async def fake(*args,**kwargs): return [{"title":"Example","url":"https://example.com/","snippet":"Snippet"}]
    monkeypatch.setattr("tools.web_search._tavily_search",fake)
    result=asyncio.run(search_web("DEEP33",provider="tavily",api_key="tvly-test"))
    assert result["results"][0]["snippet"]=="Snippet"
    assert result["engine"] == "DEEP33 Search Engine"
    assert result["provider"] == "tavily"

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


def test_tavily_falls_back_to_ddg(monkeypatch):
    monkeypatch.setenv("WEB_SEARCH_BING_ENABLED", "false")
    monkeypatch.setattr("tools.web_search.validate_public_url", lambda value: value)
    async def failing(*args,**kwargs): raise RuntimeError("down")
    async def ddg(*args,**kwargs): return [{"title":"DDG","url":"https://example.com/","snippet":"Fallback"}]
    monkeypatch.setattr("tools.web_search._tavily_search",failing)
    monkeypatch.setattr("tools.web_search._duckduckgo_search",ddg)
    import tools.web_search as module
    module._SEARCH_CACHE.clear()
    result=asyncio.run(search_web("DEEP33 fallback test",provider="tavily",api_key="tvly-test"))
    assert result["provider"]=="duckduckgo"

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
    result = asyncio.run(search_web("DEEP33", provider="bing", api_key=""))
    assert result["ok"] is True
    assert result["provider"] == "bing"
    assert result["results"][0]["url"] == "https://example.org/deep33"


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
    response = __import__("fastapi").testclient.TestClient(__import__("backend.main", fromlist=["app"]).app).get("/v1/web/status")
    assert response.status_code == 200
    body = response.json()
    assert body["tool_loop_enabled"] is True


def test_search_coalesces_concurrent_fresh_requests(monkeypatch):
    import tools.web_search as module
    from backend.search.engine import SearchEngine

    calls = {"count": 0}

    async def fake_engine_search(*_args, **_kwargs):
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

    monkeypatch.setattr(SearchEngine, "search", fake_engine_search)
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
