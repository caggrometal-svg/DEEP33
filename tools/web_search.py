from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import html
import inspect
import json
import logging
import os
import re
import time
import weakref
from threading import Lock
from html.parser import HTMLParser
from datetime import datetime, timezone
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import httpx

logger = logging.getLogger(__name__)

from tools.web_fetch import validate_public_url

DEFAULT_TAVILY_URL = "https://api.tavily.com/search"
DUCKDUCKGO_URL = "https://html.duckduckgo.com/html/"
BING_URL = "https://www.bing.com/search"
DEFAULT_TIMEOUT_SECONDS = 8.0
DEFAULT_MAX_RESULTS = 8
MAX_QUERY_CHARS = 1000
SEARCH_CACHE_TTL_SECONDS = max(
    5.0, min(300.0, float(os.getenv("WEB_SEARCH_CACHE_TTL_SECONDS", "90")))
)
_SEARCH_CACHE: dict[str, tuple[float, dict]] = {}
_SEARCH_INFLIGHT: dict[str, asyncio.Task] = {}
_SEARCH_HTTP_CLIENT = None
_SEARCH_HTTP_LOOP: asyncio.AbstractEventLoop | None = None
_SEARCH_HTTP_SEMAPHORES: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()
_SEARCH_HTTP_SEMAPHORES_LOCK = Lock()


def _http_timeout(seconds: float) -> httpx.Timeout:
    bounded = max(0.5, min(3.5, float(seconds)))
    return httpx.Timeout(
        # One second was too tight for external-provider connection setup on Render.
        # The provider and global search deadlines still cap total latency.
        connect=min(1.8, bounded),
        read=min(2.5, bounded),
        write=min(1.5, bounded),
        pool=min(2.0, bounded),
    )


class _BoundedAsyncClient:
    """Apply one process-wide per-event-loop limit to every outbound search request."""

    def __init__(self, client: httpx.AsyncClient, semaphore: asyncio.Semaphore):
        self._client = client
        self._semaphore = semaphore

    async def request(self, method, url, **kwargs):
        async with self._semaphore:
            return await self._client.request(method, url, **kwargs)

    async def get(self, url, **kwargs):
        return await self.request("GET", url, **kwargs)

    async def post(self, url, **kwargs):
        return await self.request("POST", url, **kwargs)

    async def head(self, url, **kwargs):
        return await self.request("HEAD", url, **kwargs)

    async def aclose(self):
        await self._client.aclose()

    def __getattr__(self, name):
        return getattr(self._client, name)


def _search_http_semaphore(loop: asyncio.AbstractEventLoop) -> asyncio.Semaphore:
    with _SEARCH_HTTP_SEMAPHORES_LOCK:
        semaphore = _SEARCH_HTTP_SEMAPHORES.get(loop)
        if semaphore is None:
            limit = max(
                4,
                min(32, int(os.getenv("WEB_SEARCH_HTTP_CONCURRENCY", "32"))),
            )
            semaphore = asyncio.Semaphore(limit)
            _SEARCH_HTTP_SEMAPHORES[loop] = semaphore
        return semaphore


async def _search_http_client():
    global _SEARCH_HTTP_CLIENT, _SEARCH_HTTP_LOOP
    loop = asyncio.get_running_loop()
    if _SEARCH_HTTP_CLIENT is None or _SEARCH_HTTP_LOOP is not loop:
        _SEARCH_HTTP_CLIENT = _BoundedAsyncClient(
            httpx.AsyncClient(
                timeout=_http_timeout(DEFAULT_TIMEOUT_SECONDS),
                follow_redirects=True,
                limits=httpx.Limits(
                    max_connections=40,
                    max_keepalive_connections=20,
                    keepalive_expiry=30.0,
                ),
            ),
            _search_http_semaphore(loop),
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


class YahooHeadingSearchParser(HTMLParser):
    """Extract general-web destinations from Yahoo headings without brittle CSS selectors."""

    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.results = []
        self._anchors: list[str] = []
        self._heading = None

    def handle_starttag(self, tag, attrs):
        attrs_map = dict(attrs)
        if tag == "a":
            raw_href = (attrs_map.get("href") or "").strip()
            href = _unwrap_yahoo_url(urljoin(self.base_url, raw_href)) if raw_href else ""
            if href:
                self._anchors.append(href)
                if self._heading is not None and not self._heading.get("url"):
                    self._heading["url"] = href
            return
        if tag in {"h2", "h3"}:
            self._heading = {
                "tag": tag,
                "url": self._anchors[-1] if self._anchors else "",
                "parts": [],
            }

    def handle_data(self, data):
        if self._heading is not None:
            value = " ".join(str(data or "").split()).strip()
            if value:
                self._heading["parts"].append(value)

    def handle_endtag(self, tag):
        if self._heading is not None and tag == self._heading["tag"]:
            heading = self._heading
            self._heading = None
            title = " ".join(heading["parts"]).strip()
            url = str(heading.get("url") or "").strip()
            host = (urlparse(url).hostname or "").lower().rstrip(".")
            if (
                title
                and url
                and host
                and host != "yahoo.com"
                and not host.endswith(".yahoo.com")
            ):
                self.results.append({"title": title[:300], "url": url, "snippet": ""})
        if tag == "a" and self._anchors:
            self._anchors.pop()


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


def _unwrap_yahoo_url(value: str) -> str:
    """Unwrap Yahoo's click-tracking redirects while preserving direct public URLs."""
    candidate = html.unescape(str(value or "").strip())
    if not candidate:
        return ""
    parsed = urlparse(candidate)
    host = (parsed.hostname or "").lower().rstrip(".")
    if host == "r.search.yahoo.com" or host.endswith(".search.yahoo.com"):
        targets = list(parse_qs(parsed.query).get("RU", []))
        targets.extend(
            segment[3:]
            for segment in parsed.path.split("/")
            if segment.startswith("RU=")
        )
        for raw_target in targets:
            target = raw_target
            for _ in range(3):
                target = unquote(target)
                if target.startswith(("https://", "http://")):
                    return target
    return candidate


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
        source_domain = str(item.get("source_domain") or "").strip().casefold().rstrip(".")
        if source_domain and re.fullmatch(r"[a-z0-9.-]{1,253}", source_domain) and "." in source_domain:
            normalized["source_domain"] = source_domain
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


async def _youcom_search(query, timeout_seconds, max_results):
    """Search You.com's independent web index through its keyless 100/day MCP profile."""
    client = await _search_http_client()
    protocol = "2026-07-28"
    rpc_id = 2
    body = {
        "jsonrpc": "2.0",
        "id": rpc_id,
        "method": "tools/call",
        "params": {
            "name": "you-search",
            "arguments": {
                "query": str(query or "")[:400],
                "count": max(1, min(8, int(max_results))),
                "extraction": "none",
            },
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": protocol,
                "io.modelcontextprotocol/clientInfo": {
                    "name": "DEEP33",
                    "version": "1.0",
                },
                "io.modelcontextprotocol/clientCapabilities": {},
            },
        },
    }
    response = await client.post(
        "https://api.you.com/mcp/free",
        json=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": protocol,
            "Mcp-Method": "tools/call",
            "Mcp-Name": "you-search",
            "User-Agent": "DEEP33-WebSearch/1.0",
        },
        timeout=_http_timeout(min(float(timeout_seconds), 2.5)),
    )
    if response.status_code >= 400:
        raise WebSearchError(f"WEB_SEARCH_YOUCOM_HTTP_{response.status_code}")

    content_type = str(response.headers.get("content-type") or "").casefold()
    if "application/json" in content_type:
        messages = [response.json()]
    else:
        messages = []
        raw = response.text.replace("\r\n", "\n")
        for event in raw.split("\n\n"):
            data_lines = [
                line[5:].lstrip()
                for line in event.splitlines()
                if line.startswith("data:")
            ]
            if data_lines:
                try:
                    messages.append(json.loads("\n".join(data_lines)))
                except ValueError as exc:
                    raise WebSearchError("WEB_SEARCH_YOUCOM_INVALID_SSE") from exc

    reply = next(
        (message for message in messages if isinstance(message, dict) and message.get("id") == rpc_id),
        None,
    )
    if reply is None:
        raise WebSearchError("WEB_SEARCH_YOUCOM_MISSING_RPC_REPLY")
    if "error" in reply:
        raise WebSearchError("WEB_SEARCH_YOUCOM_RPC_ERROR")
    result = reply.get("result") if isinstance(reply.get("result"), dict) else {}
    if result.get("isError"):
        raise WebSearchError("WEB_SEARCH_YOUCOM_TOOL_ERROR")

    payload = result.get("structuredContent")
    if not isinstance(payload, dict):
        text_blocks = result.get("content") if isinstance(result.get("content"), list) else []
        text_value = next(
            (block.get("text") for block in text_blocks if isinstance(block, dict) and block.get("type") == "text"),
            "",
        )
        try:
            payload = json.loads(text_value) if text_value else {}
        except ValueError as exc:
            raise WebSearchError("WEB_SEARCH_YOUCOM_INVALID_RESULT_JSON") from exc

    results_container = payload.get("results") if isinstance(payload, dict) else {}
    raw_items = []
    if isinstance(results_container, dict):
        for key in ("web", "news"):
            values = results_container.get(key)
            if isinstance(values, list):
                raw_items.extend(values)

    normalized = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        snippets = item.get("snippets")
        snippet_parts = []
        if isinstance(snippets, list):
            for snippet in snippets:
                if isinstance(snippet, str):
                    snippet_parts.append(snippet)
                elif isinstance(snippet, dict):
                    snippet_parts.append(
                        str(snippet.get("text") or snippet.get("snippet") or snippet.get("content") or "")
                    )
        contents = item.get("contents")
        if isinstance(contents, dict) and isinstance(contents.get("highlights"), list):
            snippet_parts.extend(str(value) for value in contents["highlights"] if value)
        normalized.append({
            "title": item.get("title") or "",
            "url": item.get("url") or "",
            "snippet": item.get("description") or " ".join(snippet_parts),
            "published_at": item.get("page_age") or item.get("published_date") or item.get("date") or "",
        })
    return _normalise_results(normalized, max_results)


async def _mwmbl_search(query, timeout_seconds, max_results):
    """Search Mwmbl's independent index using its anonymous no-key free tier."""
    client = await _search_http_client()
    response = await client.get(
        "https://api.mwmbl.org/api/v2/search/",
        params={"q": query},
        headers={
            "User-Agent": "DEEP33-WebSearch/1.0",
            "Accept": "application/json",
        },
        timeout=_http_timeout(min(float(timeout_seconds), 2.0)),
    )
    if response.status_code >= 400:
        raise WebSearchError(f"WEB_SEARCH_MWMBL_HTTP_{response.status_code}")
    if len(response.content) > 2 * 1024 * 1024:
        raise WebSearchError("WEB_SEARCH_MWMBL_RESPONSE_TOO_LARGE")
    try:
        payload = response.json()
    except ValueError as exc:
        raise WebSearchError("WEB_SEARCH_MWMBL_INVALID_JSON") from exc
    raw = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(raw, list):
        raise WebSearchError("WEB_SEARCH_MWMBL_INVALID_RESULTS")

    # Mwmbl can expose seed/partner results in the same response. Only accept
    # hits explicitly indexed by Mwmbl itself so it cannot masquerade as Google
    # or another external engine and inflate independent-provider quorum.
    own_index = []
    for item in raw:
        if not isinstance(item, dict) or str(item.get("engine") or "").casefold() != "mwmbl":
            continue
        own_index.append({
            "title": item.get("title") or "",
            "url": item.get("url") or "",
            "content": item.get("content") or "",
        })
    return _normalise_results(own_index, max_results)


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
            source_node = item.find("source")
            if source_node is None:
                source_node = item.find("{*}source")
            source_domain = ""
            if source_node is not None:
                source_url = (source_node.attrib.get("url") or "").strip()
                if source_url:
                    try:
                        safe_source_url = _safe_search_result_url(source_url)
                        source_domain = (urlparse(safe_source_url).hostname or "").casefold().rstrip(".")
                    except Exception:
                        source_domain = ""
            published_at = (
                item.findtext("pubDate")
                or item.findtext("{*}pubDate")
                or item.findtext("published")
                or item.findtext("{*}published")
                or item.findtext("updated")
                or item.findtext("{*}updated")
                or ""
            ).strip()
            if title and url:
                result = {"title": title, "url": url, "snippet": snippet}
                if source_domain:
                    result["source_domain"] = source_domain
                if published_at:
                    result["published_at"] = published_at[:100]
                rss_results.append(result)
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
            # Query broadly; the parsed publisher timestamp is checked
            # against the requested freshness window after extraction.
            "q": normalized_query,
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
    # Google News uses standard RSS; reuse the bounded XML parser and URL checks,
    # but retain its own provenance rather than inheriting the Bing parser name.
    results = await _parse_bing_rss(response, max_results)
    return [{**item, "provider": "google_news"} for item in results]

async def _google_news_top_stories_search(timeout_seconds, max_results):
    """Recover current Chile headlines from Google's country-scoped top-stories RSS."""
    client = await _search_http_client()
    response = await client.get(
        "https://news.google.com/rss",
        params={"hl": "es-419", "gl": "CL", "ceid": "CL:es-419"},
        headers={
            "User-Agent": "DEEP33-WebSearch/1.0",
            "Accept": "application/rss+xml,application/xml,text/xml",
        },
        timeout=_http_timeout(min(float(timeout_seconds), 2.5)),
    )
    results = await _parse_bing_rss(response, max_results)
    return [{**item, "provider": "google_news"} for item in results]


async def _bing_recent_news_search(
    query: str,
    timeout_seconds: float,
    max_results: int,
) -> list[dict]:
    """Fetch publisher-linked RSS results with publication dates from Bing."""
    client = await _search_http_client()
    response = await client.get(
        os.getenv("WEB_SEARCH_BING_URL", BING_URL),
        params={"q": query, "format": "rss", **_bing_market_params(query)},
        headers={
            "User-Agent": "DEEP33-WebSearch/1.0",
            "Accept": "application/rss+xml,application/xml,text/xml",
        },
        timeout=_http_timeout(min(2.5, max(0.8, float(timeout_seconds)))),
    )
    results = await _parse_bing_rss(response, max_results)
    return [{**item, "provider": "bing"} for item in results]


PUBLISHER_NEWS_RSS_FEEDS = (
    ("latercera.com", "https://www.latercera.com/arcio/rss/"),
    ("biobiochile.cl", "https://feeds.feedburner.com/radiobiobio/NNeJ"),
    ("eldesconcierto.cl", "https://eldesconcierto.cl/rss"),
    ("theclinic.cl", "https://www.theclinic.cl/feed/"),
    ("ciperchile.cl", "https://www.ciperchile.cl/feed/"),
    ("cooperativa.cl", "https://www.cooperativa.cl/noticias/site/tax/port/all/rss____1.xml"),
    ("df.cl", "https://www.df.cl/noticias/site/list/port/rss.xml"),
    ("publimetro.cl", "https://www.publimetro.cl/arc/outboundfeeds/rss/?outputType=xml"),
)


async def _publisher_rss_search(
    query: str,
    timeout_seconds: float,
    max_results: int,
) -> list[dict]:
    """Recover dated direct publisher articles when general search RSS is sparse."""
    client = await _search_http_client()
    # Retain enough headlines per publisher for topic ranking; selecting only
    # the first item from each general feed often misses relevant stories.
    per_feed_limit = max(1, min(8, int(max_results)))
    # Start every publisher feed in the same recovery wave so slow publishers
    # cannot starve independent feeds within the strict-news recovery budget.
    feed_semaphore = asyncio.Semaphore(max(1, min(8, len(PUBLISHER_NEWS_RSS_FEEDS))))

    async def fetch_feed(domain: str, feed_url: str) -> list[dict]:
        try:
            async with feed_semaphore:
                response = await client.get(
                    feed_url,
                    headers={
                        "User-Agent": "DEEP33-WebSearch/1.0",
                        "Accept": "application/rss+xml,application/xml,text/xml",
                    },
                    timeout=_http_timeout(min(2.5, max(0.8, float(timeout_seconds)))),
                )
            items = await _parse_bing_rss(response, per_feed_limit)
            candidates = []
            for item in items:
                url = str(item.get("url") or "").strip()
                host = (urlparse(url).hostname or "").casefold().removeprefix("www.")
                if host != domain and not host.endswith("." + domain):
                    continue
                candidates.append({
                    **item,
                    "provider": "publisher_rss",
                    "provider_sources": ["publisher_rss"],
                    "source_domain": domain,
                })
            logger.info(
                "publisher_rss_feed_fetch domain=%s status=%s parsed=%s accepted=%s",
                domain,
                getattr(response, "status_code", "unknown"),
                len(items),
                len(candidates),
            )
            return candidates
        except Exception as exc:
            logger.warning(
                "publisher_rss_feed_failed domain=%s error=%s",
                domain,
                type(exc).__name__,
            )
            return []

    settled = await asyncio.gather(
        *(fetch_feed(domain, feed_url) for domain, feed_url in PUBLISHER_NEWS_RSS_FEEDS),
        return_exceptions=True,
    )
    feed_batches = [
        result
        for result in settled
        if isinstance(result, list) and result
    ]
    # Round-robin across publisher feeds so the result cap does not let the
    # first high-volume outlet crowd every other editorial domain out.
    merged: list[dict] = []
    for item_index in range(per_feed_limit):
        for batch in feed_batches:
            if item_index < len(batch):
                merged.append(batch[item_index])
                if len(merged) >= max_results:
                    break
        if len(merged) >= max_results:
            break
    logger.info(
        "publisher_rss_recovery_complete feeds=%s candidates=%s domains=%s",
        len(PUBLISHER_NEWS_RSS_FEEDS),
        len(merged),
        len({str(item.get("source_domain") or "") for item in merged}),
    )
    return merged[:max_results]


async def _corroborate_google_news_candidates_with_indexes(
    candidates: list[dict],
    timeout_seconds: float,
    *,
    max_candidates: int = 3,
) -> None:
    """Attach Bing provenance only when Bing independently finds the same publisher article."""
    targets: list[dict] = []
    seen_domains: set[str] = set()
    for item in candidates:
        if not isinstance(item, dict) or item.get("provider") != "google_news":
            continue
        domain = str(item.get("source_domain") or "").casefold().removeprefix("www.")
        title = str(item.get("title") or "").strip()
        if not domain or not title or domain in seen_domains:
            continue
        seen_domains.add(domain)
        targets.append(item)
        if len(targets) >= max(1, min(4, int(max_candidates))):
            break

    if not targets:
        return

    jobs: list[tuple[dict, str, Any]] = []
    for item in targets:
        title_query = f'"{str(item.get("title") or "").strip()}"'
        domain_query = f'{title_query} site:{str(item.get("source_domain") or "").strip()}'
        jobs.append((item, "bing", _bing_recent_news_search(domain_query, timeout_seconds, 2)))
        # Mwmbl is a full-text index and does not reliably implement the
        # Google-style site: operator; constrain its results by publisher host
        # after retrieval instead.
        jobs.append((item, "mwmbl", _mwmbl_search(title_query, timeout_seconds, 2)))
    corroboration_semaphore = asyncio.Semaphore(4)

    async def run_corroboration_job(coroutine):
        async with corroboration_semaphore:
            return await coroutine

    settled = await asyncio.gather(
        *(run_corroboration_job(job[2]) for job in jobs),
        return_exceptions=True,
    )
    stopwords = {"chile", "noticia", "noticias", "hoy", "ultima", "ultimas", "actualidad", "de", "del", "en", "el", "la"}

    for (candidate, provider_name, _job), results in zip(jobs, settled):
        if isinstance(results, BaseException) or not isinstance(results, list):
            continue
        target_domain = str(candidate.get("source_domain") or "").casefold().removeprefix("www.")
        target_tokens = {
            token for token in re.findall(r"[a-z0-9áéíóúüñ]+", str(candidate.get("title") or "").casefold())
            if len(token) > 2 and token not in stopwords
        }
        for result in results:
            if not isinstance(result, dict):
                continue
            result_url = str(result.get("url") or "").strip()
            result_host = (urlparse(result_url).hostname or "").casefold().removeprefix("www.")
            result_domain = str(result.get("source_domain") or result_host).casefold().removeprefix("www.")
            if not result_url or result_domain != target_domain:
                continue
            result_text = " ".join((
                str(result.get("title") or ""),
                str(result.get("snippet") or ""),
                str(result.get("content") or ""),
            )).casefold()
            result_tokens = {
                token for token in re.findall(r"[a-z0-9áéíóúüñ]+", result_text)
                if len(token) > 2 and token not in stopwords
            }
            overlap = len(target_tokens & result_tokens) / max(1, len(target_tokens))
            result_path_parts = [part for part in urlparse(result_url).path.split("/") if part]
            if overlap < 0.30 or len(result_path_parts) < 2:
                continue
            providers = candidate.get("provider_sources")
            providers = providers if isinstance(providers, list) else []
            candidate["provider_sources"] = sorted({
                str(value)
                for value in [*providers, "google_news", provider_name]
                if value
            })
            candidate["providers"] = list(candidate["provider_sources"])
            logger.info(
                "recent_news_provider_corroborated domain=%s provider=%s providers=%s title=%s",
                target_domain,
                provider_name,
                candidate["provider_sources"],
                str(candidate.get("title") or "")[:140],
            )
            break



async def search_recent_news_candidates(
    query: str,
    *,
    timeout_seconds: float = 2.0,
    max_results: int = 8,
) -> list[dict]:
    """Return dated recent-news candidates from independent RSS search paths."""
    cleaned = " ".join(str(query or "").split()).strip()
    if not cleaned:
        return []
    limit = max(1, min(24, int(max_results)))
    budget = min(2.5, max(0.8, float(timeout_seconds)))
    publisher_domains = (
        "biobiochile.cl",
        "cooperativa.cl",
        "latercera.com",
        "emol.com",
        "24horas.cl",
    )
    queries: list[tuple[str, str | None]] = [(cleaned, None)]
    if re.search(r"\bchile\b", cleaned, re.IGNORECASE):
        queries.extend(
            (f"{cleaned} site:{domain}", domain)
            for domain in publisher_domains
        )

    jobs = []
    if "chile" in cleaned.casefold():
        jobs.append((
            "publisher_rss",
            None,
            _publisher_rss_search(cleaned, budget, min(limit, 24)),
        ))
        # A country-scoped top-stories feed is a separate recovery path from
        # query RSS. It supplies additional publisher domains when Google News
        # search-by-query is sparse; article freshness/relevance is still
        # validated after extraction and it cannot satisfy evidence alone.
        jobs.append((
            "google_news",
            None,
            _google_news_top_stories_search(budget, min(limit, 24)),
        ))
        # Mwmbl contributes an independent index. Its SERP may be undated;
        # the final article fetch must extract and validate publication time.
        jobs.append((
            "mwmbl",
            None,
            _mwmbl_search(cleaned, budget, min(limit, 2)),
        ))
    jobs.extend([
        ("google_news", None, _google_news_search(cleaned, budget, limit)),
        ("google_news", None, _google_news_search("noticias Chile", budget, min(limit, 3))),
        *(
            ("bing", expected_domain, _bing_recent_news_search(search_query, budget, limit))
            for search_query, expected_domain in queries
        ),
    ])
    # This recovery path bypasses the main orchestrator; bound its parallel
    # RSS/search calls to protect the shared HTTPX connection pool.
    job_semaphore = asyncio.Semaphore(6)

    async def run_news_job(coroutine):
        async with job_semaphore:
            return await coroutine

    settled = await asyncio.gather(
        *(run_news_job(job[2]) for job in jobs),
        return_exceptions=True,
    )
    # Reject dated but stale Google News items here. Otherwise its first batch
    # can fill the candidate limit and prevent independent Bing RSS recovery
    # from being considered at all.
    from backend.search.engine import _is_recently_published

    merged: list[dict] = []
    by_url: dict[str, dict] = {}
    for (default_provider, expected_domain, _job), result in zip(jobs, settled):
        if isinstance(result, BaseException) or not isinstance(result, list):
            continue
        for item in result:
            if not isinstance(item, dict):
                continue
            published_at = str(item.get("published_at") or "").strip()
            url = str(item.get("url") or "").strip()
            provider = str(default_provider or item.get("provider") or "unknown")
            if not url:
                continue
            if published_at:
                if not _is_recently_published(item, cleaned):
                    continue
            elif provider not in {"mwmbl", "google_news"}:
                continue
            else:
                parsed_candidate = urlparse(url)
                candidate_path = [part for part in parsed_candidate.path.split("/") if part]
                if (
                    parsed_candidate.scheme.lower() != "https"
                    or len(candidate_path) < 2
                    or len(str(item.get("title") or "").strip()) < 12
                    or (
                        provider == "publisher_rss"
                        and len(str(item.get("snippet") or item.get("content") or "").strip()) < 30
                    )
                ):
                    continue
            try:
                canonical_url = _safe_search_result_url(url)
            except Exception:
                continue
            hostname = (urlparse(canonical_url).hostname or "").casefold().removeprefix("www.")
            # Bing RSS can wrap the destination in a click-through URL while
            # separately identifying the publisher in <source url="...">.
            # Validate the publisher metadata when present; checking the wrapper
            # hostname here discarded every targeted publisher recovery result.
            source_domain = str(
                item.get("source_domain") or ""
            ).casefold().removeprefix("www.").rstrip(".")
            bing_click_host = hostname == "bing.com" or hostname.endswith(".bing.com")
            # Trust the RSS <source> host only while the link is still a Bing
            # click-through. For a direct URL, its actual hostname must match
            # the requested publisher; mismatched metadata is not evidence.
            publisher_domain = source_domain if bing_click_host and source_domain else hostname
            if (
                expected_domain
                and publisher_domain != expected_domain
                and not publisher_domain.endswith("." + expected_domain)
            ):
                continue

            # The dispatch job is authoritative provenance. Some adapters
            # normalize missing provider fields to "unknown", which must not
            # erase the known Mwmbl/Google News/Bing origin.
            provider = str(default_provider or item.get("provider") or "unknown")
            existing = by_url.get(canonical_url)
            if existing is not None:
                prior = existing.get("provider_sources")
                prior = prior if isinstance(prior, list) else [existing.get("provider") or default_provider]
                existing["provider_sources"] = sorted({str(value) for value in [*prior, provider] if value})
                continue
            candidate = {
                **item,
                "url": canonical_url,
                "provider": provider,
                "source_domain": str(
                    item.get("source_domain")
                    or (urlparse(canonical_url).hostname or "").casefold().removeprefix("www.")
                ),
                "provider_sources": [provider],
            }
            by_url[canonical_url] = candidate
            merged.append(candidate)

    # Corroborate Google News article candidates against Bing by exact title
    # and publisher domain. A provider is added only when Bing returns a matching
    # article; mere provider availability never satisfies the quorum.
    await _corroborate_google_news_candidates_with_indexes(
        merged,
        budget,
        max_candidates=3,
    )

    # Reserve at least one slot for each available independent search index
    # before publisher RSS recovery can fill the result cap. Publisher feeds
    # are primary-source discovery paths, not independent search providers.
    provider_priority = (
        "google_news",
        "bing",
        "mwmbl",
        "mojeek",
        "cl0q",
        "tavily",
        "marginalia",
    )
    selected: list[dict] = []
    selected_urls: set[str] = set()
    for provider_name in provider_priority:
        candidate = next(
            (item for item in merged if item.get("provider") == provider_name),
            None,
        )
        if candidate is not None:
            selected.append(candidate)
            selected_urls.add(str(candidate.get("url") or ""))
            if len(selected) >= limit:
                break
    if len(selected) < limit:
        for candidate in merged:
            url = str(candidate.get("url") or "")
            if url in selected_urls:
                continue
            selected.append(candidate)
            selected_urls.add(url)
            if len(selected) >= limit:
                break

    provider_mix: dict[str, int] = {}
    for item in selected:
        provider_name = str(item.get("provider") or "unknown")
        provider_mix[provider_name] = provider_mix.get(provider_name, 0) + 1
    logger.info(
        "recent_news_recovery_provider_mix candidates=%s providers=%s",
        len(selected),
        sorted(provider_mix.items()),
    )
    return selected


async def _bing_search(query, timeout_seconds, max_results, *, preserve_candidates=False):
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
        if html_results and not realtime_news and not preserve_candidates:
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
            if preserve_candidates:
                return html_results[:max_results]
            from backend.search.engine import is_realtime_query, rank_results
            return rank_results(query, html_results, realtime=is_realtime_query(query))[:max_results]
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

        if preserve_candidates:
            return normalized[:max_results]
        from backend.search.engine import is_realtime_query, rank_results
        return rank_results(query, normalized, realtime=is_realtime_query(query))[:max_results]
    if html_results:
        if preserve_candidates:
            return html_results[:max_results]
        from backend.search.engine import is_realtime_query, rank_results
        return rank_results(query, html_results, realtime=is_realtime_query(query))[:max_results]
    if html_error is not None:
        raise WebSearchError(str(html_error)) from html_error
    return []


async def _google_search(query, timeout_seconds, max_results, *, preserve_candidates=False):
    """Search general public web pages with Google, not a news-only feed."""
    client = await _search_http_client()
    market = _bing_market_params(query)
    google_params = {
        "q": query,
        "hl": market["setlang"],
        "gl": market["cc"].upper(),
        "num": str(min(10, max(3, int(max_results) * 2))),
    }
    if _looks_realtime_news(query):
        # Provider-side freshness filter: keep current-news retrieval inside the
        # last day instead of relying only on post-ranking freshness heuristics.
        google_params["tbs"] = "qdr:d"
    response = await client.get(
        "https://www.google.com/search",
        params=google_params,
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
    if preserve_candidates:
        return normalized[:max_results]
    from backend.search.engine import is_realtime_query, rank_results
    return rank_results(
        query,
        normalized,
        realtime=is_realtime_query(query),
    )[:max_results]


async def _yahoo_search(
    query,
    timeout_seconds,
    max_results,
    *,
    preserve_candidates=False,
):
    """Search public general-web pages through Yahoo as an independent fallback."""
    client = await _search_http_client()
    response = await client.get(
        "https://search.yahoo.com/search",
        params={"p": query, "ei": "UTF-8"},
        headers={
            "User-Agent": "DEEP33-WebSearch/1.0",
            "Accept": "text/html,application/xhtml+xml",
        },
        timeout=_http_timeout(min(float(timeout_seconds), 2.5)),
    )
    if response.status_code >= 400:
        raise WebSearchError(f"WEB_SEARCH_YAHOO_HTTP_{response.status_code}")
    if len(response.content) > 3 * 1024 * 1024:
        raise WebSearchError("WEB_SEARCH_YAHOO_RESPONSE_TOO_LARGE")
    parser = YahooHeadingSearchParser(str(response.url))
    parser.feed(response.text)
    parser.close()
    normalized = _normalise_results(parser.results, max_results)
    if preserve_candidates:
        return normalized[:max_results]
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
    from backend.search.engine import _normalise_lookup_query, is_realtime_query
    from backend.search.resilient import WebOrchestrator, get_web_orchestrator

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

    max_results = max(1, min(12, int(max_results)))
    orchestrator = get_web_orchestrator(max_results)
    # The fast flag was previously accepted but ignored. A bounded budget on
    # this path protects first-token latency; fallback still has a reserved slice.
    if fast and isinstance(orchestrator, WebOrchestrator):
        fast_budget = max(1.0, min(3.5, float(timeout_seconds)))
        result = await orchestrator.search(
            cleaned,
            fresh=fresh,
            budget_seconds=fast_budget,
        )
    else:
        # Honor the caller's search budget on the production orchestrator. The
        # previous path ignored timeout_seconds entirely, so /v1/web/search could
        # request 5 seconds while the orchestrator still used its shorter default
        # budget and returned zero candidates before slower providers completed.
        if isinstance(orchestrator, WebOrchestrator):
            full_budget = max(1.0, min(8.0, float(timeout_seconds)))
            search_parameters = inspect.signature(orchestrator.search).parameters
            if "budget_seconds" in search_parameters:
                result = await orchestrator.search(
                    cleaned,
                    fresh=fresh,
                    budget_seconds=full_budget,
                )
            else:
                # Tests and injected adapters may deliberately expose the older
                # narrow signature. Preserve compatibility without losing budget
                # propagation on the production WebOrchestrator implementation.
                result = await orchestrator.search(cleaned, fresh=fresh)
        else:
            # Preserve the narrow legacy call contract for injected adapters that
            # expose only search(query, fresh=...), without widening that contract.
            result = await orchestrator.search(cleaned, fresh=fresh)
    if not result.get("ok"):
        from backend.weather import fetch_named_location_weather, is_weather_query
        if is_weather_query(cleaned):
            weather = await fetch_named_location_weather(
                cleaned,
                timeout_seconds=min(4.0, float(timeout_seconds)),
            )
            if weather is None:
                logger.warning(
                    "web_search_weather_fallback_unavailable query=%s",
                    cleaned[:160],
                )
            if weather is not None:
                location = ", ".join(
                    part for part in (
                        str(weather.get("location_name") or "").strip(),
                        str(weather.get("country") or "").strip(),
                    ) if part
                )
                snippet = (
                    f"Condiciones actuales en {location or cleaned}: "
                    f"{weather.get('temperature_c')} °C, "
                    f"{weather.get('condition')}, humedad "
                    f"{weather.get('relative_humidity_pct')}%, viento "
                    f"{weather.get('wind_speed_kmh')} km/h. "
                    f"Observación: {weather.get('time')} ({weather.get('timezone')})."
                )
                item = {
                    "title": f"Clima actual en {location or cleaned} — Open-Meteo",
                    "url": str(weather.get("source_url") or DEFAULT_TAVILY_URL),
                    "snippet": snippet,
                    "provider": "open_meteo",
                    "source_domain": "api.open-meteo.com",
                    "published_at": str(weather.get("time") or ""),
                }
                result = copy.deepcopy(result)
                existing_candidates = [
                    candidate
                    for candidate in (
                        result.get("results")
                        if isinstance(result.get("results"), list)
                        else result.get("sources")
                        if isinstance(result.get("sources"), list)
                        else []
                    )
                    if isinstance(candidate, dict)
                ]
                combined_candidates = [item]
                seen_urls = {str(item.get("url") or "").strip()}
                for candidate in existing_candidates:
                    candidate_url = str(candidate.get("url") or "").strip()
                    if not candidate_url.startswith("https://") or candidate_url in seen_urls:
                        continue
                    combined_candidates.append(candidate)
                    seen_urls.add(candidate_url)
                    if len(combined_candidates) >= max_results:
                        break
                result.update({
                    "ok": True,
                    "status": "DEGRADED",
                    "diagnostic_code": None,
                    "results": combined_candidates,
                    "sources": combined_candidates,
                    "provider": "open_meteo",
                    "winner_provider": "open_meteo",
                    "specialist_provider": "open_meteo",
                })
                result["providers"] = list(dict.fromkeys([
                    *(result.get("providers") or []), "open_meteo"
                ]))
                result["providers_attempted"] = list(dict.fromkeys([
                    *(result.get("providers_attempted") or []), "open_meteo"
                ]))
                domains = {
                    str(candidate.get("source_domain") or (
                        urlparse(str(candidate.get("url") or "")).hostname or ""
                    )).casefold().removeprefix("www.")
                    for candidate in combined_candidates
                } - {""}
                result["verification"] = {
                    "level": "specialist-data-plus-web-context",
                    "distinct_domains": len(domains),
                    "distinct_providers": len(result["providers"]),
                    "required_domains": 2,
                    "required_providers": 2,
                    "domain_quorum_met": len(domains) >= 2,
                    # Open-Meteo is a specialist data source, not a second search index.
                    "provider_quorum_met": False,
                    "navigation_only": False,
                    "navigation_candidates_present": False,
                    "evidence_status": "DEGRADED",
                    "corroborated_results": 0,
                    "specialist_provider": "open_meteo",
                }
    result["fresh_request"] = bool(fresh)
    result["configured_provider"] = (
        provider or os.getenv("WEB_SEARCH_PROVIDER", "auto")
    ).strip().lower()
    return result


def web_search_status():
    enabled = os.getenv("DEEP33_WEB_TOOLS_ENABLED", "true").strip().lower() == "true"
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
        "enabled": enabled,
        "engine": "DEEP33 Search Engine",
        "engine_version": "1.4.0",
        "realtime": True,
        "provider_independent": True,
        "realtime_cache_bypass": True,
        "minimum_provider_families": 2,
        "minimum_publisher_domains": 2,
        "realtime_news_minimum_provider_families": 2,
        "realtime_news_minimum_publishers": 3,
        "runtime_provider_mode": "parallel_multi_provider",
        "configured_provider": provider,
        "active_provider": active,
        "tavily_configured": key,
        "bing_enabled": bing,
        "fallback_duckduckgo": True,
        "parallel_providers": True,
        "mojeek_enabled": os.getenv("WEB_SEARCH_MOJEEK_ENABLED", "true").strip().lower() == "true",
        "max_queries": 2,
        "query_strategy": "parallel_providers_with_bounded_semantic_recovery",
        "max_results": max(
            1, min(8, int(os.getenv("WEB_SEARCH_MAX_RESULTS", "8")))
        ),
        "timeout_seconds": max(
            1.0,
            min(8.0, float(os.getenv("WEB_SEARCH_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS)))),
        ),
    }
