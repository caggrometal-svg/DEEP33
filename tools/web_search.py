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
    bounded = max(0.5, float(seconds))
    return httpx.Timeout(
        connect=min(2.5, bounded),
        read=bounded,
        write=min(5.0, bounded),
        pool=min(2.0, bounded),
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
        published = item.get("published_at") or item.get("published") or item.get("date")
        if published:
            normalized["published_at"] = str(published)[:100]
        out.append(normalized)
        if len(out) >= limit:
            break
    return out


async def _tavily_search(query, api_key, timeout_seconds, max_results):
    payload = {
        "query": query,
        "search_depth": "basic",
        "topic": "general",
        "max_results": max_results,
        "include_answer": False,
        "include_raw_content": False,
        "include_images": False,
        "safe_search": False,
    }
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


async def _duckduckgo_search(query, timeout_seconds, max_results):
    client = await _search_http_client()
    response = await client.get(
        DUCKDUCKGO_URL,
        params={"q": query, "kl": "wt-wt"},
        headers={
            "User-Agent": "DEEP33-WebSearch/1.0",
            "Accept": "text/html,application/xhtml+xml",
        },
        timeout=timeout_seconds,
    )
    if response.status_code >= 400:
        raise WebSearchError(f"WEB_SEARCH_DDG_HTTP_{response.status_code}")
    if len(response.content) > 2 * 1024 * 1024:
        raise WebSearchError("WEB_SEARCH_DDG_RESPONSE_TOO_LARGE")
    parser = DuckDuckGoParser()
    parser.feed(response.text)
    parser.close()
    return _normalise_results(parser.results, max_results)


async def _parse_bing_rss(response, max_results):
    if response.status_code >= 400:
        raise WebSearchError(f"WEB_SEARCH_BING_RSS_HTTP_{response.status_code}")
    if len(response.content) > 2 * 1024 * 1024:
        raise WebSearchError("WEB_SEARCH_BING_RSS_RESPONSE_TOO_LARGE")
    try:
        from xml.etree import ElementTree

        root = ElementTree.fromstring(response.text)
        rss_results = []
        for item in root.findall(".//item"):
            title = item.findtext("title") or ""
            url = item.findtext("link") or ""
            snippet = item.findtext("description") or ""
            rss_results.append({"title": title, "url": url, "snippet": snippet})
        return _normalise_results(rss_results, max_results)
    except Exception as exc:
        raise WebSearchError("WEB_SEARCH_BING_RSS_INVALID_RESPONSE") from exc


async def _bing_search(query, timeout_seconds, max_results):
    client = await _search_http_client()
    html_error = None
    try:
        response = await client.get(
            os.getenv("WEB_SEARCH_BING_URL", BING_URL),
            params={"q": query, "setlang": "es", "cc": "cl"},
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
        results = _normalise_results(parser.results, max_results)
        if results:
            return results
    except Exception as exc:
        html_error = exc

    rss_timeout = min(timeout_seconds, 2.5)
    rss = await client.get(
        os.getenv("WEB_SEARCH_BING_URL", BING_URL),
        params={"q": query, "format": "rss", "setlang": "es", "cc": "cl"},
        headers={
            "User-Agent": "DEEP33-WebSearch/1.0",
            "Accept": "text/html,application/xhtml+xml",
        },
        timeout=timeout_seconds,
    )
    try:
        return await _parse_bing_rss(rss, max_results)
    except Exception as rss_error:
        if html_error is not None:
            raise WebSearchError(f"{html_error};{rss_error}") from rss_error
        raise


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
    cleaned = " ".join(query.split()).strip()
    if not cleaned:
        raise WebSearchError("WEB_SEARCH_QUERY_REQUIRED")
    if len(cleaned) > MAX_QUERY_CHARS:
        raise WebSearchError("WEB_SEARCH_QUERY_TOO_LONG")

    timeout_seconds = max(1.0, min(6.0, timeout_seconds))
    max_results = max(1, min(8, int(max_results)))
    selected_provider = (
        provider or os.getenv("WEB_SEARCH_PROVIDER", "auto")
    ).strip().lower()
    key = (
        api_key if api_key is not None else os.getenv("WEB_SEARCH_API_KEY", "")
    ).strip()

    from backend.search.engine import SearchEngine

    engine = SearchEngine(
        max_results=max_results,
        max_queries=(
            1
            if fast
            else max(1, min(3, int(os.getenv("WEB_SEARCH_MAX_QUERIES", "3"))))
        ),
    )
    cache_key = _search_cache_key(
        cleaned, selected_provider, key, timeout_seconds, max_results
    )
    now = time.monotonic()
    cached = _SEARCH_CACHE.get(cache_key)
    # Explicit/current web requests must reach the Internet on every call.
    # Cache remains available only for non-fresh repeated searches.
    if not fresh and cached and cached[0] > now:
        return copy.deepcopy(cached[1])

    started = time.perf_counter()
    inflight = _SEARCH_INFLIGHT.get(cache_key)
    if inflight is not None and not inflight.done():
        shared = await inflight
        return {
            **copy.deepcopy(shared),
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
        }

    task = asyncio.create_task(
        engine.search(
            cleaned,
            provider=selected_provider,
            api_key=key,
            timeout_seconds=timeout_seconds,
            fallback_ddg=os.getenv("WEB_SEARCH_FALLBACK_DDG", "true").strip().lower() == "true",
            fast_mode=fast,
        )
    )
    _SEARCH_INFLIGHT[cache_key] = task
    try:
        result = await task
        result = {
            **result,
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
        }
        if not result["ok"]:
            raise WebSearchError(
                "WEB_SEARCH_FAILED:" + ",".join(result.get("errors") or ["NO_RESULTS"])
            )
        _SEARCH_CACHE[cache_key] = (
            time.monotonic() + SEARCH_CACHE_TTL_SECONDS,
            copy.deepcopy(result),
        )
        return copy.deepcopy(result)
    finally:
        if _SEARCH_INFLIGHT.get(cache_key) is task:
            _SEARCH_INFLIGHT.pop(cache_key, None)


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
