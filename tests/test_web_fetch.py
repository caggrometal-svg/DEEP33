from __future__ import annotations

import json

from tools.web_fetch import _extract_text


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
    assert len(body) >= 80

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
