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

