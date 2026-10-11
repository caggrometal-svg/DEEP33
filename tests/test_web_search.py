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
