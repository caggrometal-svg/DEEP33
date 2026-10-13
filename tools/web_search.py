from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import html
import os
import re
import time
from html.parser import HTMLParser
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from tools.web_fetch import validate_public_url

DEFAULT_TAVILY_URL = "https://api.tavily.com/search"
DUCKDUCKGO_URL = "https://html.duckduckgo.com/html/"
BING_URL = "https://www.bing.com/search"
DEFAULT_TIMEOUT_SECONDS = 5.0
DEFAULT_MAX_RESULTS = 5
MAX_QUERY_CHARS = 1000
SEARCH_CACHE_TTL_SECONDS = max(
    5.0, min(300.0, float(os.getenv("WEB_SEARCH_CACHE_TTL_SECONDS", "90")))
)
_SEARCH_CACHE: dict[str, tuple[float, dict]] = {}
_SEARCH_INFLIGHT: dict[str, asyncio.Task] = {}
_SEARCH_HTTP_CLIENT: httpx.AsyncClient | None = None
_SEARCH_HTTP_LOOP: asyncio.AbstractEventLoop | None = None


def _http_timeout(seconds: float) -> httpx.Timeout:
    bounded = max(0.5, min(2.5, float(seconds)))
    return httpx.Timeout(
        # One second was too tight for external-provider connection setup on Render.
        # The provider and global search deadlines still cap total latency.
        connect=min(1.5, bounded),
        read=min(1.5, bounded),
        write=min(1.0, bounded),
        pool=min(0.5, bounded),
    )


async def _search_http_client() -> httpx.AsyncClient:
    global _SEARCH_HTTP_CLIENT, _SEARCH_HTTP_LOOP
    loop = asyncio.get_running_loop()
    if _SEARCH_HTTP_CLIENT is None or _SEARCH_HTTP_LOOP is not loop:
        _SEARCH_HTTP_CLIENT = httpx.AsyncClient(
            timeout=_http_timeout(DEFAULT_TIMEOUT_SECONDS),
            follow_redirects=True,
            limits=httpx.Limits(
                max_connections=20,
                max_keepalive_connections=10,
                keepalive_expiry=30.0,
            ),
        )
        _SEARCH_HTTP_LOOP = loop
    return _SEARCH_HTTP_CLIENT


async def close_search_http_client() -> None:
    global _SEARCH_HTTP_CLIENT, _SEARCH_HTTP_LOOP
    if _SEARCH_HTTP_CLIENT is not None:
        await _SEARCH_HTTP_CLIENT.aclose()
    _SEARCH_HTTP_CLIENT = None
    _SEARCH_HTTP_LOOP = None


def _search_cache_key(
    query: str,
    provider: str,
    api_key: str,
    timeout_seconds: float,
    max_results: int,
) -> str:
    raw = "\x1f".join(
        [query, provider, "configured" if api_key else "anonymous", str(timeout_seconds), str(max_results)]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()




class WebSearchError(RuntimeError):
    pass


class DuckDuckGoParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.results = []
        self._current = None
        self._capture = None
        self._buffer = []

    def _flush(self):
        if self._current and self._current.get("title") and self._current.get("url"):
            self.results.append(dict(self._current))
        self._current = None
        self._capture = None
        self._buffer = []

    def handle_starttag(self, tag, attrs):
        attrs_map = dict(attrs)
        classes = set((attrs_map.get("class") or "").split())
        if tag == "a" and "result__a" in classes:
            self._flush()
            self._current = {
                "url": _unwrap_ddg_url(attrs_map.get("href") or ""),
                "title": "",
                "snippet": "",
            }
            self._capture = "title"
            self._buffer = []
            return
        if self._current and "result__snippet" in classes:
            self._capture = "snippet"
            self._buffer = []

    def handle_endtag(self, tag):
        if not self._capture:
            return
        if self._capture == "title" and tag == "a":
            self._current["title"] = " ".join(self._buffer).strip()
            self._capture = None
            self._buffer = []
        elif self._capture == "snippet" and tag in {"div", "td"}:
            self._current["snippet"] = " ".join(self._buffer).strip()
            self._capture = None
            self._buffer = []

    def handle_data(self, data):
        if self._capture:
            value = html.unescape(data).strip()
            if value:
                self._buffer.append(value)

    def close(self):
        super().close()
        self._flush()


class BingParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.results = []
        self._current = None
        self._capture = None
        self._buffer = []
        self._in_caption = False

    def _flush(self):
        if self._current and self._current.get("title") and self._current.get("url"):
            self.results.append(dict(self._current))
        self._current = None
        self._capture = None
        self._buffer = []
        self._in_caption = False

    def handle_starttag(self, tag, attrs):
        attrs_map = dict(attrs)
        classes = set((attrs_map.get("class") or "").split())
        if tag == "li" and "b_algo" in classes:
            self._flush()
            self._current = {"url": "", "title": "", "snippet": ""}
            return
        if self._current is None:
            return
        if tag == "h2":
            self._capture = "title"
            self._buffer = []
        elif tag == "a" and self._capture == "title":
            self._current["url"] = _unwrap_bing_url(attrs_map.get("href") or "")
        elif tag == "div" and "b_caption" in classes:
            self._in_caption = True
        elif tag == "p" and self._in_caption:
            self._capture = "snippet"
            self._buffer = []

    def handle_endtag(self, tag):
        if self._capture == "title" and tag == "h2":
            self._current["title"] = " ".join(self._buffer).strip()
            self._capture = None
            self._buffer = []
        elif self._capture == "snippet" and tag == "p":
            self._current["snippet"] = " ".join(self._buffer).strip()
            self._capture = None
            self._buffer = []
        elif tag == "div" and self._in_caption:
            self._in_caption = False
        elif tag == "li":
            self._flush()

    def handle_data(self, data):
        if self._capture:
            value = html.unescape(data).strip()
            if value:
                self._buffer.append(value)

    def close(self):
        super().close()
        self._flush()



def _google_result_url(value: str) -> str:
    """Return an external destination URL from a Google search result link."""
    candidate = html.unescape(str(value or "").strip())
    if not candidate:
        return ""
    if candidate.startswith("/url?"):
        candidate = "https://www.google.com" + candidate
    parsed = urlparse(candidate)
    host = (parsed.hostname or "").lower().rstrip(".")
    if host == "google.com" or host.endswith(".google.com"):
        if parsed.path != "/url":
            return ""
        params = parse_qs(parsed.query)
        target = (params.get("q") or params.get("url") or [""])[0].strip()
        destination = urlparse(target)
        destination_host = (destination.hostname or "").lower().rstrip(".")
        if destination.scheme.lower() not in {"http", "https"} or not destination_host:
            return ""
        if destination.username or destination.password or destination.port not in (None, 80, 443):
            return ""
        if destination_host == "google.com" or destination_host.endswith(".google.com"):
            return ""
        return target
    if parsed.scheme.lower() not in {"http", "https"} or not host:
        return ""
    if parsed.username or parsed.password or parsed.port not in (None, 80, 443):
        return ""
    return candidate


class GoogleSearchParser(HTMLParser):
    """Extract public general-web results from Google's standard HTML SERP."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.results = []
        self._pending_url = ""
        self._capture_title = False
        self._buffer = []

    def handle_starttag(self, tag, attrs):
        attrs_map = dict(attrs)
        if tag == "a":
            self._pending_url = _google_result_url(attrs_map.get("href") or "")
        elif tag == "h3" and self._pending_url:
            self._capture_title = True
            self._buffer = []

    def handle_data(self, data):
        if self._capture_title:
            value = html.unescape(data).strip()
            if value:
                self._buffer.append(value)

    def handle_endtag(self, tag):
        if tag == "h3" and self._capture_title:
            title = " ".join(self._buffer).strip()
            if title and self._pending_url:
                self.results.append({
                    "title": title,
                    "url": self._pending_url,
                    "snippet": "",
                })
            self._pending_url = ""
            self._capture_title = False
            self._buffer = []


class MojeekParser(HTMLParser):
    """Small parser for Mojeek's public general-web results page."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.results = []
        self._in_results = False
        self._current = None
        self._capture = None
        self._buffer = []

    def _flush(self):
        if self._current and self._current.get("title") and self._current.get("url"):
            self.results.append(dict(self._current))
        self._current = None
        self._capture = None
        self._buffer = []

    def handle_starttag(self, tag, attrs):
        attrs_map = dict(attrs)
        classes = set((attrs_map.get("class") or "").split())
        if tag == "ul" and "results-standard" in classes:
            self._in_results = True
            return
        if not self._in_results:
            return
        if tag == "li":
            self._flush()
            self._current = {"url": "", "title": "", "snippet": ""}
            return
        if self._current is None:
            return
        if tag == "a":
            href = (attrs_map.get("href") or "").strip()
            if "ob" in classes and href:
                self._current["url"] = href
            elif self._capture == "title" and href and not self._current.get("url"):
                self._current["url"] = href
        if tag == "h2":
            self._capture = "title"
            self._buffer = []
        elif tag == "p" and "s" in classes:
            self._capture = "snippet"
            self._buffer = []

    def handle_endtag(self, tag):
        if self._capture == "title" and tag == "h2":
            self._current["title"] = " ".join(self._buffer).strip()
            self._capture = None
            self._buffer = []
        elif self._capture == "snippet" and tag == "p":
            self._current["snippet"] = " ".join(self._buffer).strip()
            self._capture = None
            self._buffer = []
        if tag == "li":
            self._flush()
        elif tag == "ul" and self._in_results:
            self._flush()
            self._in_results = False

    def handle_data(self, data):
        if self._capture:
            value = " ".join(str(data or "").split()).strip()
            if value:
                self._buffer.append(value)

    def close(self):
        super().close()
        self._flush()


def _unwrap_ddg_url(value):
    if not value:
        return ""
    absolute = (
        value
        if value.startswith(("http://", "https://"))
        else f"https:{value}" if value.startswith("//") else value
    )
    target = parse_qs(urlparse(absolute).query).get("uddg", [None])[0]
    return unquote(target) if target else absolute


def _unwrap_bing_url(value):
    if not value:
        return ""
    if value.startswith(("http://", "https://")):
        parsed = urlparse(value)
        target = parse_qs(parsed.query).get("u", [None])[0]
        if target:
            target = unquote(target)
            if target.startswith("a1"):
                try:
                    decoded = base64.urlsafe_b64decode(target[2:] + "===" ).decode("utf-8", "ignore")
                    if decoded.startswith(("http://", "https://")):
                        return decoded
                except Exception:
                    pass
            if target.startswith(("http://", "https://")):
                return target
        if parsed.netloc.lower().endswith("bing.com"):
            return ""
        return value
    return ""


def _bing_market_params(query: str) -> dict[str, str]:
    """Choose a search market from the query language rather than forcing Chilean Spanish on all topics."""
    text = " ".join(str(query or "").casefold().split())
    tokens = set(re.findall(r"[\wáéíóúüñ]+", text, flags=re.UNICODE))
    spanish_markers = {
        "actualidad", "ayer", "búsqueda", "busqueda", "chile", "chileno",
        "chilena", "clima", "comuna", "cotización", "cotizacion",
        "dólar", "dolar", "elecciones", "fútbol", "futbol", "gobierno",
        "hoy", "municipalidad", "noticia", "noticias", "presidente",
        "pronóstico", "pronostico", "región", "region", "santiago",
        "temperatura", "última", "ultimas", "último", "ultimos",
    }
    has_spanish_diacritic = bool(re.search(r"[áéíóúüñ¿¡]", text))
    if has_spanish_diacritic or tokens & spanish_markers:
        return {"setlang": "es", "cc": "cl"}
    return {"setlang": "en", "cc": "us"}


def _looks_realtime_news(query: str) -> bool:
    lowered = " ".join(str(query or "").split()).strip().lower()
    return any(
        term in lowered
        for term in (
            "situación actual", "situacion actual", "estado actual",
            "noticia", "noticias", "última hora", "ultima hora", "actualidad",
            "en desarrollo", "qué pasó", "que paso", "qué está pasando", "que esta pasando",
            "qué ocurre", "que ocurre", "qué pasa", "que pasa", "emergencia", "incidente",
            "incidentes", "contingencia", "suceso", "sucesos", "alerta", "ocurriendo",
            "sucede", "sucediendo", "último", "últimos", "última", "últimas",
        )
    )


def _safe_search_result_url(value: str) -> str:
    candidate = value.strip()
    parsed = urlparse(candidate)
    if parsed.scheme.lower() not in {"http", "https"}:
        raise WebSearchError("WEB_SEARCH_URL_SCHEME_BLOCKED")
    if parsed.username or parsed.password:
        raise WebSearchError("WEB_SEARCH_URL_CREDENTIALS_BLOCKED")
    if not parsed.hostname:
        raise WebSearchError("WEB_SEARCH_URL_HOST_REQUIRED")
    if parsed.port not in (None, 80, 443):
        raise WebSearchError("WEB_SEARCH_URL_PORT_BLOCKED")
    return candidate


def _normalise_results(results, limit):
    out = []
    seen = set()
    for item in results:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        url = str(item.get("url") or "").strip()
        snippet = str(item.get("content") or item.get("snippet") or "").strip()
        if not title or not url:
            continue
        try:
            url = _safe_search_result_url(url)
        except Exception:
            continue
        if url in seen:
            continue
        seen.add(url)
        normalized = {
            "title": title[:300],
            "url": url[:2000],
            "snippet": snippet[:1500],
        }
        published = (
            item.get("published_at")
            or item.get("published_date")
            or item.get("published")
            or item.get("date")
        )
        if published:
            normalized["published_at"] = str(published)[:100]
        out.append(normalized)
        if len(out) >= limit:
            break
    return out


async def _tavily_search(query, api_key, timeout_seconds, max_results):
    realtime_news = _looks_realtime_news(query)
    payload = {
        "query": query,
        "search_depth": "basic",
        "topic": "news" if realtime_news else "general",
        "max_results": max_results,
        "include_answer": False,
        "include_raw_content": False,
        "include_images": False,
        "safe_search": False,
    }
    if realtime_news:
        # Current-event requests are time-bounded at the provider itself.
        payload.update({
            "time_range": "day",
            "include_published_date": True,
            "filter_by_published_date": True,
        })
    client = await _search_http_client()
    response = await client.post(
        os.getenv("WEB_SEARCH_API_URL", DEFAULT_TAVILY_URL),
        json=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "DEEP33-WebSearch/1.0",
        },
        timeout=_http_timeout(timeout_seconds),
    )
    if response.status_code >= 400:
        raise WebSearchError(f"WEB_SEARCH_TAVILY_HTTP_{response.status_code}")
    data = response.json()
    raw = data.get("results")
    if not isinstance(raw, list):
        raise WebSearchError("WEB_SEARCH_TAVILY_INVALID_RESPONSE")
    return _normalise_results(raw, max_results)



async def _mojeek_search(query, timeout_seconds, max_results):
    """Query Mojeek's public web search as an independent no-key fallback."""
    client = await _search_http_client()
    response = await client.get(
        "https://www.mojeek.com/search",
        params={"q": query, "s": "0", "lang": "en", "lb": "en"},
        headers={
            "User-Agent": "DEEP33-WebSearch/1.0",
            "Accept": "text/html,application/xhtml+xml",
        },
        timeout=_http_timeout(min(float(timeout_seconds), 2.5)),
    )
    if response.status_code >= 400:
        raise WebSearchError(f"WEB_SEARCH_MOJEEK_HTTP_{response.status_code}")
    if len(response.content) > 2 * 1024 * 1024:
        raise WebSearchError("WEB_SEARCH_MOJEEK_RESPONSE_TOO_LARGE")
    parser = MojeekParser()
    parser.feed(response.text)
    parser.close()
    return _normalise_results(parser.results, max_results)


def _cl0q_result_items(payload, max_results):
    """Normalize cl0q's independent public index response into DEEP33 candidates."""
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        items = payload.get("results")
        if not isinstance(items, list):
            items = payload.get("items")
        if not isinstance(items, list):
            items = payload.get("data")
    else:
        items = None
    if not isinstance(items, list):
        raise WebSearchError("WEB_SEARCH_CL0Q_INVALID_RESPONSE")
    normalized = []
    for item in items:
        if not isinstance(item, dict):
            continue
        candidate = dict(item)
        candidate.setdefault("url", item.get("link") or item.get("canonical_url") or "")
        candidate.setdefault("title", item.get("h1") or item.get("name") or "")
        candidate.setdefault(
            "snippet",
            item.get("description") or item.get("content") or item.get("highlight") or "",
        )
        normalized.append(candidate)
    return _normalise_results(normalized, max_results)


async def _cl0q_search(query, timeout_seconds, max_results):
    """Search cl0q's independent web index; no credential or paid API is required."""
    client = await _search_http_client()
    response = await client.get(
        "https://cl0q.com/search",
        params={"q": query, "limit": max(1, min(8, int(max_results))), "safe": "1"},
        headers={
            "User-Agent": "DEEP33-WebSearch/1.0",
            "Accept": "application/json",
        },
        timeout=_http_timeout(min(float(timeout_seconds), 2.5)),
    )
    if response.status_code >= 400:
        raise WebSearchError(f"WEB_SEARCH_CL0Q_HTTP_{response.status_code}")
    if len(response.content) > 2 * 1024 * 1024:
        raise WebSearchError("WEB_SEARCH_CL0Q_RESPONSE_TOO_LARGE")
    try:
        payload = response.json()
    except ValueError as exc:
        raise WebSearchError("WEB_SEARCH_CL0Q_INVALID_JSON") from exc
    return _cl0q_result_items(payload, max_results)


async def _duckduckgo_search(query, timeout_seconds, max_results):
    client = await _search_http_client()
    realtime_news = _looks_realtime_news(query)
    response = await client.get(
        DUCKDUCKGO_URL,
        params={
            "q": query,
            "kl": "wt-wt",
            **({"df": "d"} if realtime_news else {}),
        },
        headers={
            "User-Agent": "DEEP33-WebSearch/1.0",
            "Accept": "text/html,application/xhtml+xml",
        },
        timeout=_http_timeout(timeout_seconds),
    )
    if response.status_code >= 400:
        raise WebSearchError(f"WEB_SEARCH_DDG_HTTP_{response.status_code}")
    if len(response.content) > 2 * 1024 * 1024:
        raise WebSearchError("WEB_SEARCH_DDG_RESPONSE_TOO_LARGE")
    parser = DuckDuckGoParser()
    parser.feed(response.text)
    parser.close()
    return _normalise_results(parser.results, max_results)


class _RSSSnippetParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        value = " ".join(str(data or "").split()).strip()
        if value:
            self.parts.append(value)


def _plain_rss_snippet(value: str) -> str:
    """Strip markup from RSS descriptions before they reach ranking or the model."""
    parser = _RSSSnippetParser()
    parser.feed(html.unescape(str(value or "")))
    parser.close()
    return " ".join(parser.parts).strip()[:1500]


async def _parse_bing_rss(response, max_results):
    if response.status_code >= 400:
        raise WebSearchError(f"WEB_SEARCH_BING_RSS_HTTP_{response.status_code}")
    if len(response.content) > 2 * 1024 * 1024:
        raise WebSearchError("WEB_SEARCH_BING_RSS_RESPONSE_TOO_LARGE")
    try:
        from xml.etree import ElementTree

        root = ElementTree.fromstring(response.text)
        items = (
            root.findall(".//item")
            or root.findall(".//{*}item")
            or root.findall(".//{*}entry")
        )
        rss_results = []
        for item in items:
            title = (
                item.findtext("title")
                or item.findtext("{*}title")
                or ""
            ).strip()
            link_node = item.find("link")
            if link_node is None:
                link_node = item.find("{*}link")
            url = ""
            if link_node is not None:
                url = (link_node.text or link_node.attrib.get("href") or "").strip()
                # Bing RSS may retain a click-through URL (ck/a?u=a1...).
                # Unwrap it before URL validation and topic scoring so the
                # canonical destination, not "bing.com", is used as evidence.
                url = _unwrap_bing_url(url) or url
            snippet = (
                item.findtext("description")
                or item.findtext("{*}description")
                or item.findtext("{*}summary")
                or item.findtext("{*}content")
                or ""
            ).strip()
            snippet = _plain_rss_snippet(snippet)
            if title and url:
                rss_results.append({"title": title, "url": url, "snippet": snippet})
        normalized = _normalise_results(rss_results, max_results)
        if not normalized:
            root_name = str(root.tag).rsplit("}", 1)[-1].lower()
            if root_name not in {"rss", "feed", "rdf"}:
                raise WebSearchError(
                    f"WEB_SEARCH_BING_RSS_UNEXPECTED_ROOT:{root_name[:50]}"
                )
        return normalized
    except WebSearchError:
        raise
    except Exception as exc:
        raise WebSearchError("WEB_SEARCH_BING_RSS_INVALID_RESPONSE") from exc


async def _google_news_search(query, timeout_seconds, max_results):
    """Search recent Spanish-language Chilean news without an API key."""
    if not _looks_realtime_news(query):
        return []
    client = await _search_http_client()
    normalized_query = " ".join(str(query or "").split()).strip()
    response = await client.get(
        "https://news.google.com/rss/search",
        params={
            "q": f"{normalized_query} when:1d",
            "hl": "es-419",
            "gl": "CL",
            "ceid": "CL:es-419",
        },
        headers={
            "User-Agent": "DEEP33-WebSearch/1.0",
            "Accept": "application/rss+xml,application/xml,text/xml",
        },
        timeout=_http_timeout(min(float(timeout_seconds), 2.5)),
    )
    # Google News uses standard RSS; reuse the bounded XML parser and URL checks.
    return await _parse_bing_rss(response, max_results)


async def _bing_search(query, timeout_seconds, max_results):
    client = await _search_http_client()
    html_error = None
    html_results = []
    realtime_news = _looks_realtime_news(query)
    try:
        response = await client.get(
            os.getenv("WEB_SEARCH_BING_URL", BING_URL),
            params={"q": query, **_bing_market_params(query)},
            headers={
                "User-Agent": "DEEP33-WebSearch/1.0",
                "Accept": "text/html,application/xhtml+xml",
            },
            timeout=_http_timeout(timeout_seconds),
        )
        if response.status_code >= 400:
            raise WebSearchError(f"WEB_SEARCH_BING_HTTP_{response.status_code}")
        if len(response.content) > 3 * 1024 * 1024:
            raise WebSearchError("WEB_SEARCH_BING_RESPONSE_TOO_LARGE")
        parser = BingParser()
        parser.feed(response.text)
        parser.close()
        html_results = _normalise_results(parser.results, max_results)
        # Don't treat syntactically valid HTML as a successful search if its
        # results don't match the requested topic. In that case, try the RSS
        # representation of the same broad-web provider before failing over.
        # This keeps the fast path for relevant HTML while rescuing polluted SERPs.
        if html_results and not realtime_news:
            from backend.search.engine import (
                MIN_FALLBACK_RESULTS,
                is_realtime_query,
                rank_results,
            )

            relevant_html = rank_results(
                query,
                html_results,
                realtime=is_realtime_query(query),
            )
            relevant_domains = {
                (urlparse(str(item.get("url") or "")).hostname or "").lower().removeprefix("www.")
                for item in relevant_html
                if item.get("url")
            }
            required_coverage = min(max_results, MIN_FALLBACK_RESULTS)
            if len(relevant_html) >= required_coverage and len(relevant_domains) >= required_coverage:
                return relevant_html[:max_results]
    except Exception as exc:
        html_error = exc

    rss_timeout = min(timeout_seconds, 2.5)
    try:
        rss = await client.get(
            os.getenv("WEB_SEARCH_BING_URL", BING_URL),
            params={"q": query, "format": "rss", **_bing_market_params(query)},
            headers={
                "User-Agent": "DEEP33-WebSearch/1.0",
                "Accept": "application/rss+xml,application/xml,text/xml",
            },
            timeout=_http_timeout(rss_timeout),
        )
        rss_results = await _parse_bing_rss(rss, max_results)
    except Exception as rss_error:
        if html_results:
            # RSS can fail after HTML returned an unrelated result page. Never
            # let the transport failure bypass topic relevance filtering.
            from backend.search.engine import is_realtime_query, rank_results

            return rank_results(
                query,
                html_results,
                realtime=is_realtime_query(query),
            )[:max_results]
        if html_error is not None:
            raise WebSearchError(f"{html_error};{rss_error}") from rss_error
        raise

    if rss_results:
        # Interleave sources so neither format crowds out the other, then
        # reject off-topic candidates before they leave the Bing provider.
        combined = []
        for index in range(max(len(rss_results), len(html_results))):
            if index < len(rss_results):
                combined.append(rss_results[index])
            if index < len(html_results):
                combined.append(html_results[index])
        normalized = _normalise_results(combined, max_results)
        from backend.search.engine import is_realtime_query, rank_results

        return rank_results(
            query,
            normalized,
            realtime=is_realtime_query(query),
        )[:max_results]
    if html_results:
        from backend.search.engine import is_realtime_query, rank_results

        return rank_results(
            query,
            html_results,
            realtime=is_realtime_query(query),
        )[:max_results]
    if html_error is not None:
        raise WebSearchError(str(html_error)) from html_error
    return []


async def _google_search(query, timeout_seconds, max_results):
    """Search general public web pages with Google, not a news-only feed."""
    client = await _search_http_client()
    market = _bing_market_params(query)
    response = await client.get(
        "https://www.google.com/search",
        params={
            "q": query,
            "hl": market["setlang"],
            "gl": market["cc"].upper(),
            "num": str(min(10, max(3, int(max_results) * 2))),
        },
        headers={
            "User-Agent": "DEEP33-WebSearch/1.0",
            "Accept": "text/html,application/xhtml+xml",
        },
        timeout=_http_timeout(timeout_seconds),
    )
    if response.status_code >= 400:
        raise WebSearchError(f"WEB_SEARCH_GOOGLE_HTTP_{response.status_code}")
    if len(response.content) > 3 * 1024 * 1024:
        raise WebSearchError("WEB_SEARCH_GOOGLE_RESPONSE_TOO_LARGE")
    parser = GoogleSearchParser()
    parser.feed(response.text)
    parser.close()
    normalized = _normalise_results(parser.results, max_results)
    from backend.search.engine import is_realtime_query, rank_results

    return rank_results(
        query,
        normalized,
        realtime=is_realtime_query(query),
    )[:max_results]


async def search_web(
    query,
    *,
    timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
    max_results=DEFAULT_MAX_RESULTS,
    provider=None,
    api_key=None,
    fast: bool = False,
    fresh: bool = False,
):
    from backend.search.engine import _normalise_lookup_query
    from backend.search.resilient import get_web_orchestrator

    raw_query = " ".join(str(query or "").split()).strip()
    cleaned = _normalise_lookup_query(raw_query) or raw_query
    if not cleaned:
        raise WebSearchError("WEB_SEARCH_QUERY_REQUIRED")
    if len(cleaned) > MAX_QUERY_CHARS:
        first_sentence = re.split(r"(?<=[.!?])\s+", cleaned, maxsplit=1)[0].strip()
        if first_sentence and len(first_sentence) <= MAX_QUERY_CHARS:
            cleaned = first_sentence
        else:
            cleaned = cleaned[:MAX_QUERY_CHARS].rsplit(" ", 1)[0].rstrip(" ?¡!.,;:")
    if not cleaned:
        raise WebSearchError("WEB_SEARCH_QUERY_REQUIRED")

    max_results = max(1, min(8, int(max_results)))
    orchestrator = get_web_orchestrator(max_results)
    result = await orchestrator.search(cleaned, fresh=fresh)
    result["fresh_request"] = bool(fresh)
    result["configured_provider"] = (
        provider or os.getenv("WEB_SEARCH_PROVIDER", "auto")
    ).strip().lower()
    return result


def web_search_status():
    provider = os.getenv("WEB_SEARCH_PROVIDER", "auto").strip().lower()
    key = bool(os.getenv("WEB_SEARCH_API_KEY", "").strip())
    bing = os.getenv("WEB_SEARCH_BING_ENABLED", "true").strip().lower() == "true"
    active = (
        "tavily"
        if provider == "tavily" and key
        else "bing"
        if provider == "auto" and bing
        else "bing"
        if provider == "bing" and bing
        else "duckduckgo"
    )
    return {
        "enabled": True,
        "engine": "DEEP33 Search Engine",
        "engine_version": "1.3.0",
        "configured_provider": provider,
        "active_provider": active,
        "tavily_configured": key,
        "bing_enabled": bing,
        "fallback_duckduckgo": os.getenv(
            "WEB_SEARCH_FALLBACK_DDG", "true"
        ).lower() == "true",
        "parallel_providers": os.getenv(
            "WEB_SEARCH_PARALLEL_PROVIDERS", "true"
        ).lower() == "true",
        "max_queries": max(
            1, min(3, int(os.getenv("WEB_SEARCH_MAX_QUERIES", "3")))
        ),
        "max_results": max(
            1, min(8, int(os.getenv("WEB_SEARCH_MAX_RESULTS", "5")))
        ),
        "timeout_seconds": max(
            1.0,
            min(6.0, float(os.getenv("WEB_SEARCH_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS)))),
        ),
    }
