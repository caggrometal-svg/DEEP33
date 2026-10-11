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
