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
