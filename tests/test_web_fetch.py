from __future__ import annotations

import json

from tools.web_fetch import (
    _extract_published_at,
    _extract_reader_published_at,
    _extract_text,
)


def test_extracts_open_graph_description_from_client_rendered_article() -> None:
    html = b"""
    <!doctype html>
    <html><head>
      <title>Publisher shell</title>
      <meta property="og:title" content="Chile: current report">
      <meta property="og:description" content="This article describes the verified events, relevant context, and current developments for readers who need a useful factual summary.">
    </head><body><div id="app"></div></body></html>
    """
    title, text = _extract_text(html, "text/html; charset=utf-8", 5000)

    assert title == "Publisher shell"
    assert "verified events" in text
    assert len(text) >= 80


def test_prefers_visible_article_body_over_metadata_description() -> None:
    visible = (
        "Visible article body contains the actual reporting, named locations, "
        "dates, contextual explanation, and detailed facts for readers. "
    ) * 3
    html = (
        '<html><head><title>Visible report</title>'
        '<meta name="description" content="Metadata summary that must not replace the full article body.">'
        '</head><body><article><p>' + visible + '</p></article></body></html>'
    ).encode()

    title, text = _extract_text(html, "text/html", 5000)

    assert title == "Visible report"
    assert text.startswith("Visible article body")
    assert len(text) > 80


def test_uses_json_ld_article_body_when_visible_page_is_only_an_app_shell() -> None:
    article_body = (
        "Structured article text includes the report's central facts, "
        "location, background, responsible organizations, and the sequence "
        "of events needed to understand what happened. "
    ) * 2
    structured = {
        "@context": "https://schema.org",
        "@type": "NewsArticle",
        "headline": "Structured article headline",
        "articleBody": article_body,
    }
    html = (
        "<html><head><script type='application/ld+json'>"
        + json.dumps(structured)
        + "</script></head><body><div id='app'></div></body></html>"
    ).encode()

    title, text = _extract_text(html, "text/html", 5000)

    assert title == "Structured article headline"
    assert text.startswith("Structured article text")
    assert len(text) >= 80


def test_does_not_treat_browser_challenge_metadata_as_article_content() -> None:
    html = b"""
    <html><head>
      <title>Security check</title>
      <meta name="description" content="Please enable JavaScript to view this page.">
    </head><body><div id="app"></div></body></html>
    """

    title, text = _extract_text(html, "text/html", 5000)

    assert title == "Security check"
    assert text == ""


def test_title_alone_is_not_misreported_as_readable_page_text() -> None:
    html = b"<html><head><title>Only a page title</title></head><body></body></html>"

    title, text = _extract_text(html, "text/html", 5000)

    assert title == "Only a page title"
    assert text == ""


def test_reader_parser_accepts_real_article_text_and_rejects_challenge_content() -> None:
    from tools.web_fetch import _parse_reader_markdown

    content = (
        "Title: Chile current report\n"
        "URL Source: https://news.example.cl/article\n"
        "Markdown Content:\n"
        + (
            "The article reports current developments in Chile, describes the "
            "people and institutions involved, provides the relevant timeline, "
            "and explains the confirmed public information in detail. "
        ) * 2
    ).encode()

    parsed = _parse_reader_markdown(content, 5000)
    assert parsed is not None
    title, body = parsed
    assert title == "Chile current report"
    assert "current developments in Chile" in body
    assert len(body) > 80

    challenge = (
        "Title: Security check\n"
        "URL Source: https://news.example.cl/article\n"
        "Markdown Content:\n"
        "Please enable JavaScript to view this page. "
        "Please enable JavaScript to view this page. "
    ).encode()
    assert _parse_reader_markdown(challenge, 5000) is None


def test_reader_fallback_policy_never_bypasses_ssrf_or_download_limits() -> None:
    from tools.web_fetch import (
        DownloadLimitError,
        SSRFBlockedError,
        WebFetchError,
        _reader_fallback_allowed,
    )

    assert _reader_fallback_allowed(WebFetchError("WEB_FETCH_HTTP_403"))
    assert _reader_fallback_allowed(WebFetchError("WEB_FETCH_TIMEOUT"))
    assert _reader_fallback_allowed(WebFetchError("WEB_FETCH_SHORT_TEXT"))
    assert _reader_fallback_allowed(WebFetchError("WEB_FETCH_HTTP_503"))
    assert not _reader_fallback_allowed(WebFetchError("WEB_FETCH_HTTP_404"))
    assert not _reader_fallback_allowed(SSRFBlockedError("WEB_FETCH_HOST_PRIVATE"))
    assert not _reader_fallback_allowed(DownloadLimitError("WEB_FETCH_DOWNLOAD_LIMIT"))


def test_fetch_page_uses_reader_only_after_direct_access_fails(monkeypatch) -> None:
    import asyncio
    from tools import web_fetch

    url = "https://news.example.cl/current-report"
    monkeypatch.setattr(web_fetch, "validate_public_url", lambda candidate: candidate)

    async def direct_failure(**kwargs):
        raise web_fetch.WebFetchError("WEB_FETCH_HTTP_403")

    async def reader_success(**kwargs):
        return {
            "ok": True,
            "url": url,
            "final_url": url,
            "title": "Current report",
            "text": "Verified article content. " * 12,
            "content_type": "text/plain",
            "bytes": 300,
            "redirects": 0,
            "retrieved_at": "2026-10-13T00:00:00+00:00",
            "reader_fallback": "jina_reader",
            "direct_fetch_error": "WEB_FETCH_HTTP_403",
        }

    monkeypatch.setattr(web_fetch, "_fetch_page_uncached", direct_failure)
    monkeypatch.setattr(web_fetch, "_fetch_with_reader_fallback", reader_success)
    # Exercise the shared cache path with a unique URL and remove old in-flight state.
    web_fetch._FETCH_CACHE.clear()
    web_fetch._FETCH_INFLIGHT.clear()

    async def exercise():
        return await web_fetch.fetch_page(url, fresh=True)

    result = asyncio.run(exercise())
    assert result["ok"] is True
    assert result["reader_fallback"] == "jina_reader"
    assert len(result["text"]) >= 80


def test_fetch_page_uses_reader_when_direct_response_has_only_short_text(monkeypatch) -> None:
    import asyncio
    from tools import web_fetch

    url = "https://news.example.cl/chile-current"
    article_body = (
        "This article contains real reporting about current developments in Chile, "
        "the institutions involved, relevant dates, verified public statements, "
        "and additional context needed to understand the event. "
    ) * 2
    reader_content = (
        "Title: Chile current report\n"
        "URL Source: https://news.example.cl/chile-current\n"
        "Markdown Content:\n"
        + article_body
    ).encode()

    monkeypatch.setattr(web_fetch, "validate_public_url", lambda candidate: candidate)

    async def direct_short(**kwargs):
        return {
            "ok": True,
            "url": url,
            "final_url": url,
            "title": "Chile current report",
            "text": "Today: current news.",
            "content_type": "text/html",
            "bytes": 90,
            "redirects": 0,
            "retrieved_at": "2026-10-13T00:00:00+00:00",
        }

    class ReaderResponse:
        status_code = 200
        headers = {"content-type": "text/plain; charset=utf-8"}

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def aiter_bytes(self):
            yield reader_content

    class ReaderClient:
        def stream(self, method, reader_url, **kwargs):
            assert method == "GET"
            assert reader_url == f"https://r.jina.ai/{url}"
            assert "params" not in kwargs
            assert kwargs["follow_redirects"] is False
            return ReaderResponse()

    async def reader_client():
        return ReaderClient()

    monkeypatch.setattr(web_fetch, "_fetch_page_uncached", direct_short)
    monkeypatch.setattr(web_fetch, "_fetch_http_client", reader_client)
    web_fetch._FETCH_CACHE.clear()
    web_fetch._FETCH_INFLIGHT.clear()

    result = asyncio.run(web_fetch.fetch_page(url, fresh=True))

    assert result["ok"] is True
    assert result["reader_fallback"] == "jina_reader"
    assert len(result["text"]) > 80
    assert "real reporting about current developments in Chile" in result["text"]
    assert result["retrieved_at"]


def test_short_direct_text_is_not_accepted_when_reader_cannot_extract_article(monkeypatch) -> None:
    import asyncio
    import pytest
    from tools import web_fetch

    url = "https://news.example.cl/short-shell"
    monkeypatch.setattr(web_fetch, "validate_public_url", lambda candidate: candidate)

    async def direct_short(**kwargs):
        return {
            "ok": True,
            "url": url,
            "final_url": url,
            "title": "Only shell text",
            "text": "Please enable JavaScript to continue.",
            "content_type": "text/html",
            "bytes": 80,
            "redirects": 0,
            "retrieved_at": "2026-10-13T00:00:00+00:00",
        }

    class BlockedReaderResponse:
        status_code = 503
        headers = {"content-type": "text/plain; charset=utf-8"}

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def aiter_bytes(self):
            yield b"temporarily unavailable"

    class ReaderClient:
        def stream(self, method, reader_url, **kwargs):
            return BlockedReaderResponse()

    async def reader_client():
        return ReaderClient()

    monkeypatch.setattr(web_fetch, "_fetch_page_uncached", direct_short)
    monkeypatch.setattr(web_fetch, "_fetch_http_client", reader_client)
    web_fetch._FETCH_CACHE.clear()
    web_fetch._FETCH_INFLIGHT.clear()

    with pytest.raises(web_fetch.WebFetchError, match="WEB_FETCH_SHORT_TEXT"):
        asyncio.run(web_fetch.fetch_page(url, fresh=True))


def test_extracts_json_api_evidence_as_readable_text() -> None:
    payload = {
        "current": {
            "time": "2026-10-13T01:00",
            "temperature_2m": 21.4,
            "relative_humidity_2m": 40,
        }
    }
    title, text = _extract_text(
        json.dumps(payload).encode("utf-8"),
        "application/json; charset=utf-8",
        5000,
    )
    assert title == ""
    assert '"temperature_2m": 21.4' in text
    assert "2026-10-13T01:00" in text


def test_fetch_page_enforces_one_total_budget_across_direct_and_reader(monkeypatch) -> None:
    import asyncio
    import time
    import pytest
    from tools import web_fetch

    url = "https://example.com/slow-reader"
    monkeypatch.setattr(web_fetch, "validate_public_url", lambda candidate: candidate)

    async def slow_pipeline(**kwargs):
        await asyncio.sleep(2.0)
        return {
            "ok": True,
            "url": url,
            "final_url": url,
            "title": "late",
            "text": "late evidence " * 20,
            "content_type": "text/plain",
            "bytes": 100,
            "redirects": 0,
            "retrieved_at": "2026-10-13T00:00:00+00:00",
        }

    monkeypatch.setattr(web_fetch, "_fetch_with_reader_fallback", slow_pipeline)
    web_fetch._FETCH_CACHE.clear()
    web_fetch._FETCH_INFLIGHT.clear()

    started = time.perf_counter()
    with pytest.raises(web_fetch.WebFetchError, match="WEB_FETCH_TIMEOUT"):
        asyncio.run(web_fetch.fetch_page(url, timeout_seconds=1.0, fresh=True))
    elapsed = time.perf_counter() - started

    assert elapsed < 1.1


def test_reader_fallback_allows_method_not_allowed() -> None:
    from tools.web_fetch import WebFetchError, _reader_fallback_allowed

    assert _reader_fallback_allowed(WebFetchError("WEB_FETCH_HTTP_405")) is True



def test_extraction_removes_common_chrome_noise() -> None:
    html = b"""
    <html><head><title>Clean report</title></head><body>
      <nav>Home Politics Markets Subscribe Now</nav>
      <article>
        <p>The verified report states that the system completed the deployment successfully.</p>
        <p>The same report records the exact production revision and its observed runtime status.</p>
      </article>
      <footer>Privacy Policy Terms of Use All Rights Reserved</footer>
    </body></html>
    """
    title, text = _extract_text(html, "text/html", 5000)
    assert title == "Clean report"
    assert "completed the deployment successfully" in text
    assert "Subscribe Now" not in text
    assert "Privacy Policy" not in text
    assert "All Rights Reserved" not in text


def test_transient_http_fetch_error_is_retryable_but_timeout_uses_reader() -> None:
    from tools.web_fetch import WebFetchError, _retryable_direct_fetch_error

    # Timeouts/network failures should release the shared budget to Reader instead
    # of repeating the same slow direct request.
    assert not _retryable_direct_fetch_error(WebFetchError("WEB_FETCH_TIMEOUT"))
    assert not _retryable_direct_fetch_error(WebFetchError("WEB_FETCH_NETWORK_ERROR"))
    assert _retryable_direct_fetch_error(WebFetchError("WEB_FETCH_HTTP_429"))
    assert _retryable_direct_fetch_error(WebFetchError("WEB_FETCH_HTTP_503"))
    assert not _retryable_direct_fetch_error(WebFetchError("WEB_FETCH_HTTP_403"))
    assert not _retryable_direct_fetch_error(WebFetchError("WEB_FETCH_PARSER_FAIL"))


def test_reader_pipeline_retries_one_transient_direct_get(monkeypatch) -> None:
    import asyncio
    from tools import web_fetch

    calls = {"direct": 0}
    url = "https://example.com/retry-once"

    async def direct(**kwargs):
        calls["direct"] += 1
        if calls["direct"] == 1:
            raise web_fetch.WebFetchError("WEB_FETCH_HTTP_503")
        return {
            "ok": True,
            "url": url,
            "final_url": url,
            "title": "Recovered",
            "text": "Recovered factual article evidence after one transient timeout. " * 3,
            "content_type": "text/html",
            "bytes": 400,
            "redirects": 0,
            "retrieved_at": "2026-10-16T00:00:00+00:00",
        }

    monkeypatch.setattr(web_fetch, "_fetch_page_uncached", direct)
    result = asyncio.run(web_fetch._fetch_with_reader_fallback(
        original_url=url,
        current_url=url,
        timeout_seconds=2.0,
        max_redirects=3,
        max_text_chars=5000,
        user_agent="DEEP33-test",
    ))

    assert result["ok"] is True
    assert calls["direct"] == 2
    assert "Recovered factual article evidence" in result["text"]

def test_direct_timeout_transfers_to_reader_without_retrying(monkeypatch) -> None:
    import asyncio
    from tools import web_fetch

    url = "https://news.example.cl/slow-current-report"
    article_body = (
        "Reader extracted current reporting with the article facts, dates, "
        "relevant organizations, context, and statements needed to verify the story. "
    ) * 3
    reader_content = (
        "Title: Current report\n"
        f"URL Source: {url}\n"
        "Markdown Content:\n"
        + article_body
    ).encode()

    monkeypatch.setattr(web_fetch, "validate_public_url", lambda candidate: candidate)
    direct_timeouts: list[float] = []

    async def direct_timeout(**kwargs):
        direct_timeouts.append(float(kwargs["timeout_seconds"]))
        raise web_fetch.WebFetchError("WEB_FETCH_TIMEOUT")

    class ReaderResponse:
        status_code = 200
        headers = {"content-type": "text/plain; charset=utf-8"}

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def aiter_bytes(self):
            yield reader_content

    class ReaderClient:
        def stream(self, method, reader_url, **kwargs):
            assert method == "GET"
            assert reader_url == f"https://r.jina.ai/{url}"
            assert kwargs["follow_redirects"] is False
            return ReaderResponse()

    async def reader_client():
        return ReaderClient()

    monkeypatch.setattr(web_fetch, "_fetch_page_uncached", direct_timeout)
    monkeypatch.setattr(web_fetch, "_fetch_http_client", reader_client)
    web_fetch._FETCH_CACHE.clear()
    web_fetch._FETCH_INFLIGHT.clear()

    result = asyncio.run(
        web_fetch.fetch_page(
            url,
            timeout_seconds=3.3,
            fresh=True,
        )
    )

    assert len(direct_timeouts) == 1
    assert abs(direct_timeouts[0] - 0.825) < 0.001
    assert result["ok"] is True
    assert result["reader_fallback"] == "jina_reader"
    assert "Reader extracted current reporting" in result["text"]
    assert len(result["text"]) > 80


def test_google_news_wrapper_decoder_returns_verified_publisher_url(monkeypatch) -> None:
    import asyncio
    from tools import web_fetch

    wrapper = "https://news.google.com/rss/articles/TEST_ARTICLE_ID?oc=5"
    target = "https://www.emol.com/noticias/Nacional/2026/10/25/current-report.html"
    inner_response = json.dumps(["garturlres", target])

    class Response:
        def __init__(self, status_code, text, headers=None):
            self.status_code = status_code
            self.text = text
            self.headers = headers or {}

    class Client:
        async def get(self, url, **kwargs):
            assert url == "https://news.google.com/rss/articles/TEST_ARTICLE_ID"
            assert "CONSENT=PENDING+987" in kwargs["headers"]["Cookie"]
            return Response(
                200,
                '<html><c-wiz><div data-n-a-sg="signed-value" data-n-a-ts="1792920000"></div></c-wiz></html>',
            )

        async def post(self, url, **kwargs):
            assert url == "https://news.google.com/_/DotsSplashUi/data/batchexecute"
            assert kwargs["headers"]["X-Same-Domain"] == "1"
            outer = json.loads(kwargs["data"]["f.req"])
            rpc_name, encoded_inner = outer[0][0]
            assert rpc_name == "Fbv4je"
            decoded_inner = json.loads(encoded_inner)
            assert decoded_inner[0] == "garturlreq"
            assert decoded_inner[2] == "TEST_ARTICLE_ID"
            assert decoded_inner[3] == 1792920000
            assert decoded_inner[4] == "signed-value"
            body = ")]}'\n\n" + json.dumps(
                [["wrb.fr", "Fbv4je", inner_response, None]]
            )
            return Response(200, body)

    async def client():
        return Client()

    monkeypatch.setattr(web_fetch, "_fetch_http_client", client)
    monkeypatch.setattr(web_fetch, "validate_public_url", lambda value: value)

    resolved = asyncio.run(
        web_fetch._resolve_google_news_wrapper(wrapper, timeout_seconds=2.0)
    )
    assert resolved == target


def test_google_news_wrapper_decoder_fails_closed_without_signed_params(monkeypatch) -> None:
    import asyncio
    import pytest
    from tools import web_fetch

    wrapper = "https://news.google.com/rss/articles/OPAQUE_ID?oc=5"

    class Response:
        status_code = 200
        text = "<html><body>Before you continue to Google</body></html>"
        headers = {}

    class Client:
        async def get(self, url, **kwargs):
            return Response()

    async def client():
        return Client()

    monkeypatch.setattr(web_fetch, "_fetch_http_client", client)

    with pytest.raises(web_fetch.WebFetchError, match="GOOGLE_NEWS_WRAPPER_PARAMS_MISSING"):
        asyncio.run(
            web_fetch._resolve_google_news_wrapper(wrapper, timeout_seconds=2.0)
        )



def test_extracts_published_at_from_final_article_html_metadata() -> None:
    html = b"""
    <html><head>
      <meta property="article:published_time" content="2026-10-27T09:30:00-03:00">
      <meta property="article:modified_time" content="2026-10-27T12:00:00Z">
    </head><body><article><p>Publisher article content.</p></article></body></html>
    """

    assert _extract_published_at(html, "text/html; charset=utf-8") == "2026-10-27T12:30:00+00:00"


def test_extracts_published_at_from_article_json_ld() -> None:
    structured = {
        "@context": "https://schema.org",
        "@type": "NewsArticle",
        "headline": "Chile news report",
        "datePublished": "2026-10-27T08:15:00-03:00",
        "dateModified": "2026-10-27T11:50:00-03:00",
    }
    html = (
        "<html><head><script type='application/ld+json'>"
        + json.dumps(structured)
        + "</script></head><body><article><p>Real article text.</p></article></body></html>"
    ).encode()

    assert _extract_published_at(html, "text/html") == "2026-10-27T11:15:00+00:00"


def test_does_not_infer_publication_date_from_modified_date_or_retrieval_time() -> None:
    structured = {
        "@context": "https://schema.org",
        "@type": "NewsArticle",
        "headline": "Chile report",
        "dateModified": "2026-10-27T11:50:00Z",
    }
    html = (
        "<html><head><script type='application/ld+json'>"
        + json.dumps(structured)
        + "</script></head><body><article><p>Article text.</p></article></body></html>"
    ).encode()

    assert _extract_published_at(html, "text/html") is None


def test_reader_publication_date_requires_matching_original_article_url() -> None:
    content = (
        "Title: Current article\n"
        "URL Source: https://publisher.example/news/story-1\n"
        "Published Time: 2026-10-27T08:00:00-03:00\n"
        "Markdown Content:\n"
        "Article body with enough text to be readable and useful."
    ).encode()

    assert _extract_reader_published_at(
        content,
        "https://publisher.example/news/story-1",
    ) == "2026-10-27T11:00:00+00:00"
    assert _extract_reader_published_at(
        content,
        "https://publisher.example/news/story-2",
    ) is None
