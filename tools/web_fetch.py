from __future__ import annotations

import asyncio
import copy
import hashlib
import html
import json
import ipaddress
import os
import re
import socket
import time
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse
from datetime import datetime, timezone

import httpx

MAX_REDIRECTS=3
MAX_RESPONSE_BYTES=512*1024

def _max_response_bytes() -> int:
    try:
        return max(64*1024, min(MAX_RESPONSE_BYTES, int(os.getenv("WEB_FETCH_MAX_RESPONSE_BYTES", str(MAX_RESPONSE_BYTES)))))
    except ValueError:
        return MAX_RESPONSE_BYTES
MAX_TEXT_CHARS=50_000
DEFAULT_TIMEOUT_SECONDS=5.0
DEFAULT_USER_AGENT="DEEP33-WebFetcher/1.0"
FETCH_CACHE_TTL_SECONDS = max(
    30.0, min(600.0, float(os.getenv("WEB_FETCH_CACHE_TTL_SECONDS", "300")))
)
_FETCH_CACHE: dict[str, tuple[float, dict]] = {}
_FETCH_INFLIGHT: dict[str, asyncio.Task] = {}
_FETCH_HTTP_CLIENT: httpx.AsyncClient | None = None
_FETCH_HTTP_LOOP: asyncio.AbstractEventLoop | None = None


async def _fetch_http_client() -> httpx.AsyncClient:
    global _FETCH_HTTP_CLIENT, _FETCH_HTTP_LOOP
    loop = asyncio.get_running_loop()
    if _FETCH_HTTP_CLIENT is None or _FETCH_HTTP_LOOP is not loop:
        _FETCH_HTTP_CLIENT = httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=min(1.0, DEFAULT_TIMEOUT_SECONDS),
                read=min(1.5, DEFAULT_TIMEOUT_SECONDS),
                write=min(1.5, DEFAULT_TIMEOUT_SECONDS),
                pool=1.0,
            ),
            follow_redirects=False,
            limits=httpx.Limits(
                max_connections=20,
                max_keepalive_connections=10,
                keepalive_expiry=30.0,
            ),
        )
        _FETCH_HTTP_LOOP = loop
    return _FETCH_HTTP_CLIENT


async def close_fetch_http_client() -> None:
    global _FETCH_HTTP_CLIENT, _FETCH_HTTP_LOOP
    if _FETCH_HTTP_CLIENT is not None:
        await _FETCH_HTTP_CLIENT.aclose()
    _FETCH_HTTP_CLIENT = None
    _FETCH_HTTP_LOOP = None


BLOCKED_HOSTNAMES={"localhost","localhost.localdomain","ip6-localhost","ip6-loopback","metadata","metadata.google.internal","instance-data","instance-data.ec2.internal","169.254.169.254","100.100.100.200"}
REDIRECT_STATUSES={301,302,303,307,308}
ALLOWED_CONTENT_TYPES={"text/html","application/xhtml+xml","text/plain","application/json"}
BLOCKED_HTML_TAGS={"script","style","noscript","svg","canvas","template","nav","footer","form","button","select","option","iframe","dialog"}

class WebFetchError(RuntimeError): pass
class SSRFBlockedError(WebFetchError): pass
class DownloadLimitError(WebFetchError): pass

NOISE_LINE_MARKERS = (
    "accept all cookies", "accept cookies", "cookie preferences", "manage cookies",
    "privacy policy", "terms of use", "terms and conditions", "subscribe now",
    "sign up for", "newsletter", "advertisement", "sponsored content",
    "enable notifications", "push notifications", "all rights reserved",
)


def _clean_extracted_text(value: str, *, max_chars: int) -> str:
    """Remove common site chrome/noise while preserving article semantics and order."""
    output: list[str] = []
    seen_short: set[str] = set()
    for raw in str(value or "").splitlines():
        line = " ".join(html.unescape(raw).split()).strip()
        if not line:
            continue
        lowered = line.casefold()
        if len(line) <= 180 and any(marker in lowered for marker in NOISE_LINE_MARKERS):
            continue
        # Repeated short chrome labels/menus add tokens but no evidence.
        fingerprint = re.sub(r"\W+", " ", lowered).strip()
        if len(line) <= 100 and fingerprint:
            if fingerprint in seen_short:
                continue
            seen_short.add(fingerprint)
        output.append(line)
        if sum(len(item) + 1 for item in output) >= max_chars:
            break
    return "\n".join(output)[:max_chars].strip()


def _retryable_direct_fetch_error(exc: WebFetchError) -> bool:
    code = str(exc)
    # A timeout or network error should transfer to Reader immediately; retrying
    # the same direct fetch consumes the one caller-visible budget and starves fallback.
    prefix = "WEB_FETCH_HTTP_"
    if code.startswith(prefix):
        try:
            status = int(code[len(prefix):].split("_", 1)[0])
        except ValueError:
            return False
        return status in {408, 425, 429} or 500 <= status <= 599
    return False

class HTMLTextExtractor(HTMLParser):
    """Extract visible HTML and conservative, server-rendered article metadata."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title_parts: list[str] = []
        self.meta_titles: dict[str, str] = {}
        self.meta_descriptions: dict[str, str] = {}
        self.meta_dates: dict[str, str] = {}
        self.jsonld_blocks: list[str] = []
        self._skip_depth = 0
        self._in_title = False
        self._jsonld_active = False
        self._jsonld_buffer: list[str] = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        values = {
            str(key).lower(): str(value or "")
            for key, value in attrs
            if key
        }

        if tag == "script" and "ld+json" in values.get("type", "").lower():
            self._jsonld_active = True
            self._jsonld_buffer = []
            return

        if tag == "meta":
            key = (
                values.get("property")
                or values.get("name")
                or values.get("itemprop")
                or ""
            ).strip().lower()
            value = " ".join(html.unescape(values.get("content", "")).split())
            if value and key in {"og:title", "twitter:title"}:
                self.meta_titles.setdefault(key, value)
            elif value and key in {
                "description", "og:description", "twitter:description",
                "article:description",
            }:
                self.meta_descriptions.setdefault(key, value)
            elif value and key in {
                "article:published_time", "article:published", "datepublished",
                "datePublished", "publishdate", "date",
            }:
                self.meta_dates.setdefault(key.casefold(), value)
            return

        if tag == "title":
            self._in_title = True
            return
        if tag in BLOCKED_HTML_TAGS:
            self._skip_depth += 1
            return
        if tag in {
            "p", "div", "section", "article", "main", "aside", "header",
            "footer", "nav", "li", "ul", "ol", "h1", "h2", "h3", "h4",
            "h5", "h6", "br", "tr", "td", "th", "pre", "blockquote",
        }:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == "script" and self._jsonld_active:
            block = "".join(self._jsonld_buffer).strip()
            if block:
                self.jsonld_blocks.append(block)
            self._jsonld_active = False
            self._jsonld_buffer = []
            return
        if tag == "title":
            self._in_title = False
            return
        if tag in BLOCKED_HTML_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if tag in {
            "p", "div", "section", "article", "main", "aside", "header",
            "footer", "nav", "li", "ul", "ol", "h1", "h2", "h3", "h4",
            "h5", "h6", "br", "tr", "td", "th", "pre", "blockquote",
        }:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._jsonld_active:
            self._jsonld_buffer.append(data)
            return
        if self._skip_depth:
            return
        value = " ".join(str(data or "").split())
        if not value:
            return
        if self._in_title:
            self.title_parts.append(value)
            return
        self.parts.append(value)

    @property
    def title(self):
        return " ".join(self.title_parts).strip()

    @property
    def meta_title(self):
        return (
            self.meta_titles.get("og:title")
            or self.meta_titles.get("twitter:title")
            or ""
        ).strip()

    @property
    def meta_description(self):
        return (
            self.meta_descriptions.get("og:description")
            or self.meta_descriptions.get("description")
            or self.meta_descriptions.get("twitter:description")
            or self.meta_descriptions.get("article:description")
            or ""
        ).strip()

    @property
    def published_at(self):
        return (
            self.meta_dates.get("article:published_time")
            or self.meta_dates.get("article:published")
            or self.meta_dates.get("datepublished")
            or self.meta_dates.get("publishdate")
            or self.meta_dates.get("date")
            or ""
        ).strip()

    @property
    def text(self):
        lines = []
        blank = False
        for raw in "\n".join(self.parts).splitlines():
            line = " ".join(raw.split()).strip()
            if not line:
                if lines and not blank:
                    lines.append("")
                blank = True
                continue
            lines.append(line)
            blank = False
        return "\n".join(lines).strip()


def _usable_fallback_text(value: str, *, minimum: int = 24) -> str:
    candidate = " ".join(html.unescape(str(value or "")).split()).strip()
    if len(candidate) < minimum:
        return ""
    lowered = candidate.casefold()
    challenge_markers = (
        "enable javascript",
        "javascript is disabled",
        "just a moment",
        "access denied",
        "verify you are human",
        "checking your browser",
        "complete the captcha",
        "request blocked",
        "403 forbidden",
        "robot check",
    )
    if any(marker in lowered for marker in challenge_markers):
        return ""
    return candidate


def _structured_article_fields(blocks: list[str]) -> tuple[str, str, str, str]:
    """Return conservative articleBody, description, and headline JSON-LD candidates."""
    article_bodies: list[tuple[int, str]] = []
    descriptions: list[tuple[int, str]] = []
    headlines: list[tuple[int, str]] = []
    publication_dates: list[tuple[int, str]] = []

    def visit(value) -> None:
        if isinstance(value, list):
            for item in value:
                visit(item)
            return
        if not isinstance(value, dict):
            return

        raw_type = value.get("@type", [])
        if isinstance(raw_type, str):
            types = [raw_type]
        elif isinstance(raw_type, list):
            types = [str(item) for item in raw_type]
        else:
            types = []
        priority = 0 if any("article" in item.casefold() for item in types) else 1

        body = value.get("articleBody")
        if isinstance(body, str):
            normalized = " ".join(html.unescape(body).split()).strip()
            if normalized:
                article_bodies.append((priority, normalized))

        description = value.get("description")
        if isinstance(description, str):
            normalized = _usable_fallback_text(description)
            if normalized:
                descriptions.append((priority, normalized))

        headline = value.get("headline") or value.get("name")
        if isinstance(headline, str):
            normalized = " ".join(html.unescape(headline).split()).strip()
            if normalized:
                headlines.append((priority, normalized))

        for date_key in ("datePublished", "datepublished"):
            date_value = value.get(date_key)
            if isinstance(date_value, str) and date_value.strip():
                publication_dates.append((priority, date_value.strip()))
        for key, child in value.items():
            if key not in {"articleBody", "description", "headline", "name", "datePublished", "datepublished"}:
                visit(child)

    for block in blocks:
        try:
            visit(json.loads(block))
        except (TypeError, ValueError):
            continue

    article_bodies.sort(key=lambda item: (item[0], -len(item[1])))
    descriptions.sort(key=lambda item: (item[0], -len(item[1])))
    headlines.sort(key=lambda item: (item[0], -len(item[1])))
    publication_dates.sort(key=lambda item: item[0])
    return (
        article_bodies[0][1] if article_bodies else "",
        descriptions[0][1] if descriptions else "",
        headlines[0][1] if headlines else "",
        publication_dates[0][1] if publication_dates else "",
    )

def _is_public_ip(value):
    try: return ipaddress.ip_address(value).is_global
    except ValueError: return False

def _validate_hostname(hostname):
    host=hostname.rstrip(".").lower()
    if not host or host in BLOCKED_HOSTNAMES: raise SSRFBlockedError("WEB_FETCH_SSRF_BLOCKED")
    try: ip=ipaddress.ip_address(host)
    except ValueError: return
    if not ip.is_global: raise SSRFBlockedError("WEB_FETCH_PRIVATE_IP_BLOCKED")

def _resolve_public_addresses(hostname):
    _validate_hostname(hostname)
    try:
        literal=ipaddress.ip_address(hostname); return [str(literal)]
    except ValueError: pass
    try: infos=socket.getaddrinfo(hostname,443,type=socket.SOCK_STREAM)
    except socket.gaierror as exc: raise WebFetchError("WEB_FETCH_DNS_FAILED") from exc
    addresses=sorted({info[4][0] for info in infos})
    if not addresses: raise WebFetchError("WEB_FETCH_DNS_EMPTY")
    if not all(_is_public_ip(a) for a in addresses): raise SSRFBlockedError("WEB_FETCH_DNS_PRIVATE_IP_BLOCKED")
    return addresses

def validate_public_url(url):
    candidate=url.strip(); parsed=urlparse(candidate)
    if parsed.scheme.lower() not in {"http","https"}: raise SSRFBlockedError("WEB_FETCH_SCHEME_BLOCKED")
    if parsed.username or parsed.password: raise SSRFBlockedError("WEB_FETCH_CREDENTIALS_BLOCKED")
    if not parsed.hostname: raise WebFetchError("WEB_FETCH_HOST_REQUIRED")
    if parsed.port not in (None,80,443): raise SSRFBlockedError("WEB_FETCH_PORT_BLOCKED")
    _resolve_public_addresses(parsed.hostname)
    return candidate

async def _read_limited(response,limit):
    """Read at most limit bytes and stop cleanly on large HTML/text pages.

    Search results often point at publisher pages whose full HTML is larger than
    the extraction budget. Returning a bounded prefix lets us extract the title
    and leading article text without downloading an unbounded response.
    """
    total=0
    chunks=[]
    async for chunk in response.aiter_bytes():
        remaining=limit-total
        if remaining<=0:
            break
        if len(chunk)>remaining:
            chunks.append(chunk[:remaining])
            break
        chunks.append(chunk)
        total+=len(chunk)
    return b"".join(chunks)

def _extract_text(content, content_type, max_text_chars):
    charset = "utf-8"
    lower = content_type.lower()
    if "charset=" in lower:
        charset = lower.split("charset=", 1)[1].split(";", 1)[0].strip() or charset
    decoded = content.decode(charset, errors="replace")
    mime = content_type.split(";", 1)[0].strip().lower()
    if mime == "text/plain":
        plain = "\n".join(line.strip() for line in decoded.splitlines() if line.strip())
        return "", _clean_extracted_text(plain, max_chars=max_text_chars)
    if mime == "application/json":
        try:
            parsed = json.loads(decoded)
            plain = json.dumps(parsed, ensure_ascii=False, indent=2)
        except json.JSONDecodeError:
            plain = decoded
        return "", plain[:max_text_chars]

    parser = HTMLTextExtractor()
    parser.feed(decoded)
    parser.close()
    visible_text = _clean_extracted_text(parser.text, max_chars=max_text_chars)
    article_body, structured_description, structured_title, structured_published_at = _structured_article_fields(
        parser.jsonld_blocks
    )
    title = (parser.title or parser.meta_title or structured_title).strip()[:500]
    published_at = (parser.published_at or structured_published_at).strip()

    # Visible text remains the primary evidence. Metadata is only a bounded
    # fallback for publisher pages whose main article is rendered in JavaScript.
    if len(visible_text) >= 80:
        return title, visible_text

    structured_body = _usable_fallback_text(article_body, minimum=80)
    if structured_body:
        return title, structured_body[:max_text_chars]

    for candidate in (structured_description, parser.meta_description):
        usable = _usable_fallback_text(candidate)
        if usable:
            return title, usable[:max_text_chars]

    if len(visible_text) >= 24:
        return title, visible_text
    return title, visible_text


def _extract_published_at(content: bytes, content_type: str) -> str:
    mime = content_type.split(";", 1)[0].strip().lower()
    if mime not in {"text/html", "application/xhtml+xml"}:
        return ""
    decoded = content.decode("utf-8", errors="replace")
    parser = HTMLTextExtractor()
    parser.feed(decoded)
    parser.close()
    _body, _description, _title, structured_published_at = _structured_article_fields(parser.jsonld_blocks)
    return (parser.published_at or structured_published_at).strip()


def _reader_published_at(content: bytes) -> str:
    for line in content.decode("utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if stripped.casefold().startswith("published time:"):
            return stripped.partition(":")[2].strip()
    return ""


def _parse_reader_markdown(content: bytes, max_text_chars: int) -> tuple[str, str] | None:
    """Return real article text from Reader output; never treat challenge pages as evidence."""
    decoded = content.decode("utf-8", errors="replace").strip()
    if not decoded:
        return None

    lines = decoded.splitlines()
    title = ""
    published_at = ""
    body_start = 0
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not title and stripped.casefold().startswith("title:"):
            title = stripped.partition(":")[2].strip()[:500]
        if not published_at and stripped.casefold().startswith("published time:"):
            published_at = stripped.partition(":")[2].strip()
        if stripped.casefold() in {"markdown content:", "markdown content"}:
            body_start = index + 1
            break

    if body_start:
        body = "\n".join(lines[body_start:]).strip()
    else:
        body = "\n".join(
            line for line in lines
            if not line.strip().casefold().startswith(
                ("title:", "url source:", "published time:", "warning:")
            )
        ).strip()

    if len(body) <= 80:
        return None
    folded = body.lstrip().casefold()
    blocked_prefixes = (
        "access denied", "403 forbidden", "404 not found", "page not found",
        "please enable javascript", "just a moment...", "attention required",
        "verify you are human", "this site can’t be reached",
        "this site can't be reached", "too many requests", "failed to fetch url",
        "error: unable to access", "robot or human?",
    )
    if folded.startswith(blocked_prefixes):
        return None
    if "cloudflare ray id" in folded[:700] and "checking your browser" in folded[:700]:
        return None
    return title, body[:max(1000, min(MAX_TEXT_CHARS, max_text_chars))]


def _reader_fallback_allowed(exc: WebFetchError) -> bool:
    """Only retry page-access failures through Reader; never bypass SSRF controls."""
    if isinstance(exc, (SSRFBlockedError, DownloadLimitError)):
        return False
    code = str(exc)
    if code in {
        "WEB_FETCH_TIMEOUT",
        "WEB_FETCH_NETWORK_ERROR",
        "WEB_FETCH_EMPTY_TEXT",
        "WEB_FETCH_SHORT_TEXT",
        "WEB_FETCH_CONTENT_TYPE_BLOCKED",
    }:
        return True
    prefix = "WEB_FETCH_HTTP_"
    if code.startswith(prefix):
        try:
            status = int(code[len(prefix):].split("_", 1)[0])
        except ValueError:
            return False
        return status in {403, 405, 408, 429} or 500 <= status <= 599
    return False


async def _fetch_with_reader_fallback(
    *,
    original_url: str,
    current_url: str,
    timeout_seconds: float,
    max_redirects: int,
    max_text_chars: int,
    user_agent: str,
) -> dict:
    direct_error: WebFetchError
    # GET is idempotent, so one bounded retry is safe for transient transport/5xx
    # failures. Never retry policy/SSRF/parser failures and never create an
    # unbounded retry loop before Reader fallback.
    for attempt in range(2):
        try:
            direct_result = await _fetch_page_uncached(
                original_url=original_url,
                current_url=current_url,
                timeout_seconds=timeout_seconds,
                max_redirects=max_redirects,
                max_text_chars=max_text_chars,
                user_agent=user_agent,
            )
            # HTTP 200 is not sufficient evidence when the source yields only a
            # short shell/metadata fragment. Let Reader try to extract real content.
            if len(str(direct_result.get("text") or "").strip()) > 80:
                return direct_result
            direct_error = WebFetchError("WEB_FETCH_SHORT_TEXT")
            break
        except WebFetchError as direct_exception:
            if not _reader_fallback_allowed(direct_exception):
                raise
            direct_error = direct_exception
            if attempt == 0 and _retryable_direct_fetch_error(direct_exception):
                await asyncio.sleep(min(0.12, max(0.03, timeout_seconds * 0.03)))
                continue
            break

    if not _reader_fallback_allowed(direct_error):
        raise direct_error

    # Reader receives only the original URL after the normal public-host
    # validation in fetch_page. Its output is accepted only when actual,
    # non-challenge article text can be extracted.
    try:
        raw_timeout = float(os.getenv("WEB_FETCH_READER_TIMEOUT_SECONDS", "4.0"))
    except ValueError:
        raw_timeout = 4.0
    reader_timeout = max(2.0, min(4.5, raw_timeout))
    timeout = httpx.Timeout(
        connect=min(1.0, reader_timeout),
        read=reader_timeout,
        write=min(1.0, reader_timeout),
        pool=min(0.5, reader_timeout),
    )
    client = await _fetch_http_client()
    try:
        async with client.stream(
            "GET",
            f"https://r.jina.ai/{original_url}",
            headers={
                "User-Agent": user_agent,
                "Accept": "text/plain",
            },
            timeout=timeout,
            follow_redirects=False,
        ) as response:
            if response.status_code >= 400:
                raise WebFetchError(f"WEB_FETCH_READER_HTTP_{response.status_code}")
            if response.status_code in REDIRECT_STATUSES:
                raise WebFetchError("WEB_FETCH_READER_REDIRECT_BLOCKED")
            content = await _read_limited(response, min(256 * 1024, _max_response_bytes()))
            mime = response.headers.get("content-type", "text/plain").split(";", 1)[0].strip().lower()
        if mime not in {"text/plain", "text/markdown", "text/x-markdown"}:
            raise WebFetchError("WEB_FETCH_READER_CONTENT_TYPE_BLOCKED")
        parsed = _parse_reader_markdown(content, max_text_chars)
        if parsed is None:
            raise WebFetchError("WEB_FETCH_READER_EMPTY_OR_BLOCKED")
        title, text = parsed
        published_at = _reader_published_at(content)
        return {
            "ok": True,
            "url": original_url,
            "final_url": original_url,
            "title": title or original_url,
            "text": text,
            "content_type": mime,
            "bytes": len(content),
            "redirects": 0,
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "published_at": published_at or None,
            "reader_fallback": "jina_reader",
            "direct_fetch_error": str(direct_error)[:100],
        }
    except httpx.TimeoutException as reader_error:
        raise direct_error from reader_error
    except httpx.HTTPError as reader_error:
        raise direct_error from reader_error
    except WebFetchError as reader_error:
        raise direct_error from reader_error


async def _fetch_with_total_budget(
    *,
    original_url: str,
    current_url: str,
    timeout_seconds: float,
    direct_timeout_seconds: float | None = None,
    max_redirects: int,
    max_text_chars: int,
    user_agent: str,
) -> dict:
    """Keep direct fetch + Reader fallback inside one caller-visible deadline."""
    safety_margin = min(0.35, max(0.10, timeout_seconds * 0.10))
    budget_seconds = max(0.75, timeout_seconds - safety_margin)
    try:
        return await asyncio.wait_for(
            _fetch_with_reader_fallback(
                original_url=original_url,
                current_url=current_url,
                timeout_seconds=(
                    direct_timeout_seconds
                    if direct_timeout_seconds is not None
                    else timeout_seconds
                ),
                max_redirects=max_redirects,
                max_text_chars=max_text_chars,
                user_agent=user_agent,
            ),
            timeout=budget_seconds,
        )
    except asyncio.TimeoutError as exc:
        raise WebFetchError("WEB_FETCH_TIMEOUT") from exc


def _is_google_news_wrapper(url: str) -> bool:
    parsed = urlparse(str(url or "").strip())
    host = (parsed.hostname or "").casefold().removeprefix("www.")
    return (
        host == "news.google.com"
        and (
            parsed.path.startswith("/rss/articles/")
            or parsed.path.startswith("/articles/")
            or parsed.path.startswith("/read/")
        )
    )


async def _resolve_google_news_wrapper(url: str, timeout_seconds: float) -> str:
    """Resolve an opaque Google News RSS URL to the original publisher URL."""
    if not _is_google_news_wrapper(url):
        return url

    parsed = urlparse(url)
    article_id = parsed.path.rstrip("/").rsplit("/", 1)[-1].strip()
    if not article_id or len(article_id) > 4096:
        raise WebFetchError("GOOGLE_NEWS_WRAPPER_INVALID")

    client = await _fetch_http_client()
    request_timeout = httpx.Timeout(
        connect=min(1.0, max(0.2, timeout_seconds)),
        read=min(1.6, max(0.4, timeout_seconds)),
        write=min(1.2, max(0.4, timeout_seconds)),
        pool=min(0.5, max(0.2, timeout_seconds)),
    )
    browser_headers = {
        "User-Agent": (
            "Mozilla/5.0 (Linux; Android 16) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/141.0.0.0 Mobile Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "es-CL,es;q=0.9,en;q=0.7",
        "Cookie": "CONSENT=PENDING+987",
    }

    splash_url = f"https://news.google.com/rss/articles/{article_id}"
    response = await client.get(
        splash_url,
        headers=browser_headers,
        timeout=request_timeout,
    )
    # Google may canonicalize the splash URL with locale parameters. Follow at
    # most one same-origin redirect and never follow /sorry/ or an external hop.
    if response.status_code in REDIRECT_STATUSES:
        location = response.headers.get("location") or ""
        next_url = urljoin(splash_url, location)
        next_parsed = urlparse(next_url)
        if (
            (next_parsed.hostname or "").casefold().removeprefix("www.") != "news.google.com"
            or "/sorry/" in next_parsed.path.casefold()
        ):
            raise WebFetchError("GOOGLE_NEWS_WRAPPER_UNRESOLVED")
        response = await client.get(
            next_url,
            headers=browser_headers,
            timeout=request_timeout,
        )
    if response.status_code != 200:
        raise WebFetchError(f"GOOGLE_NEWS_WRAPPER_HTTP_{response.status_code}")

    body = response.text
    signature_match = re.search(r'data-n-a-sg=["\']([^"\']+)["\']', body)
    timestamp_match = re.search(r'data-n-a-ts=["\']([0-9]+)["\']', body)
    if not signature_match or not timestamp_match:
        raise WebFetchError("GOOGLE_NEWS_WRAPPER_PARAMS_MISSING")

    signature = html.unescape(signature_match.group(1))
    timestamp = int(timestamp_match.group(1))
    inner = json.dumps(
        [
            "garturlreq",
            [
                ["X", "X", ["X", "X"], None, None, 1, 1, "US:en", None, 1, None, None, None, None, None, 0, 1],
                "X", "X", 1, [1, 1, 1], 1, 1, None, 0, 0, None, 0,
            ],
            article_id,
            timestamp,
            signature,
        ],
        separators=(",", ":"),
    )
    outer = json.dumps([[["Fbv4je", inner]]], separators=(",", ":"))
    rpc = await client.post(
        "https://news.google.com/_/DotsSplashUi/data/batchexecute",
        headers={
            **browser_headers,
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            "Origin": "https://news.google.com",
            "Referer": "https://news.google.com/",
            "X-Same-Domain": "1",
        },
        data={"f.req": outer},
        timeout=request_timeout,
    )
    if rpc.status_code != 200:
        raise WebFetchError(f"GOOGLE_NEWS_WRAPPER_RPC_HTTP_{rpc.status_code}")

    payload_text = rpc.text
    if "\n\n" in payload_text:
        payload_text = payload_text.split("\n\n", 1)[1]
    try:
        frames = json.loads(payload_text)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise WebFetchError("GOOGLE_NEWS_WRAPPER_RPC_INVALID") from exc

    decoded_url = ""
    if isinstance(frames, list):
        for frame in frames:
            if (
                isinstance(frame, list)
                and len(frame) >= 3
                and frame[0] == "wrb.fr"
                and frame[1] == "Fbv4je"
                and frame[2]
            ):
                try:
                    decoded = json.loads(frame[2])
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
                if (
                    isinstance(decoded, list)
                    and len(decoded) >= 2
                    and decoded[0] == "garturlres"
                ):
                    decoded_url = str(decoded[1] or "").strip()
                    break

    if not decoded_url:
        raise WebFetchError("GOOGLE_NEWS_WRAPPER_UNRESOLVED")

    decoded_parsed = urlparse(decoded_url)
    decoded_host = (decoded_parsed.hostname or "").casefold().removeprefix("www.")
    if (
        decoded_parsed.scheme.casefold() not in {"http", "https"}
        or not decoded_host
        or decoded_host == "news.google.com"
        or decoded_host == "google.com"
        or decoded_host.endswith(".google.com")
    ):
        raise WebFetchError("GOOGLE_NEWS_WRAPPER_UNSAFE_DESTINATION")

    return await asyncio.to_thread(validate_public_url, decoded_url)


async def _fetch_page_uncached(
    *,
    original_url: str,
    current_url: str,
    timeout_seconds: float,
    max_redirects: int,
    max_text_chars: int,
    user_agent: str,
) -> dict:
    # Keep connection establishment bounded, but do not cap every body read at 1s.
    # The total direct-fetch + Reader fallback deadline remains enforced by
    # _fetch_with_total_budget, so this widens the useful extraction window safely.
    timeout=httpx.Timeout(
        connect=min(timeout_seconds, 1.0),
        read=min(timeout_seconds, max(2.5, timeout_seconds * 0.75)),
        write=min(timeout_seconds, 2.0),
        pool=min(timeout_seconds, 0.5),
    )
    client=await _fetch_http_client()
    headers={"User-Agent":user_agent,"Accept":"text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.1"}
    redirects=[]
    for hop in range(max_redirects+1):
        current_url=await asyncio.to_thread(validate_public_url, current_url)
        try:
            async with client.stream("GET",current_url,headers=headers,timeout=timeout) as response:
                if response.status_code in REDIRECT_STATUSES:
                    location=response.headers.get("location")
                    if not location: raise WebFetchError("WEB_FETCH_REDIRECT_LOCATION_MISSING")
                    if hop>=max_redirects: raise WebFetchError("WEB_FETCH_REDIRECT_LIMIT")
                    next_url=urljoin(current_url,location); await asyncio.to_thread(validate_public_url, next_url)
                    redirects.append(next_url); current_url=next_url; continue
                if response.status_code>=400: raise WebFetchError(f"WEB_FETCH_HTTP_{response.status_code}")
                content_type=response.headers.get("content-type",""); mime=content_type.split(";",1)[0].strip().lower()
                if mime not in ALLOWED_CONTENT_TYPES: raise WebFetchError("WEB_FETCH_CONTENT_TYPE_BLOCKED")
                # Content-Length is only a hint. Some publisher pages exceed
                # the extraction cap while their useful article text is at the
                # start; _read_limited enforces the same hard byte ceiling.
                content=await _read_limited(response,_max_response_bytes())
            try:
                title, text = _extract_text(content, content_type, max_text_chars)
                published_at = _extract_published_at(content, content_type)
            except Exception as exc:
                # Surface parser/charset failures with a stable diagnosis so the
                # evidence pipeline can log the request ID, original URL and provider.
                raise WebFetchError("WEB_FETCH_PARSER_FAIL") from exc
            text = text.strip()
            if len(text) < 20:
                raise WebFetchError("WEB_FETCH_EMPTY_TEXT")
            return {
                "ok":True,
                "url":original_url,
                "final_url":current_url,
                "title":title or current_url,
                "text":text,
                "content_type":mime,
                "bytes":len(content),
                "redirects":len(redirects),
                "retrieved_at":datetime.now(timezone.utc).isoformat(),
                "published_at":published_at or None,
            }
        except httpx.TimeoutException as exc:
            raise WebFetchError("WEB_FETCH_TIMEOUT") from exc
        except httpx.HTTPError as exc:
            raise WebFetchError("WEB_FETCH_NETWORK_ERROR") from exc
    raise WebFetchError("WEB_FETCH_REDIRECT_LIMIT")


async def fetch_page(
    url,
    *,
    timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
    direct_timeout_seconds: float | None = None,
    max_redirects=MAX_REDIRECTS,
    max_text_chars=MAX_TEXT_CHARS,
    user_agent=DEFAULT_USER_AGENT,
    fresh=False,
):
    if not 1 <= max_redirects <= MAX_REDIRECTS:
        raise ValueError("max_redirects must be between 1 and 3")
    timeout_seconds = max(1.0, min(8.0, timeout_seconds))
    # Reserve most of the total deadline for Jina Reader by default. Explicit
    # callers may provide a smaller direct budget when their upstream has a
    # tighter response deadline (for example the Supabase Edge proxy).
    if direct_timeout_seconds is None:
        direct_timeout = max(0.5, min(1.2, timeout_seconds * 0.25))
    else:
        direct_timeout = max(
            0.5,
            min(2.0, timeout_seconds, float(direct_timeout_seconds)),
        )
    max_text_chars = max(1000, min(MAX_TEXT_CHARS, max_text_chars))
    original_url = url.strip()
    current_url = await asyncio.to_thread(validate_public_url, original_url)
    if _is_google_news_wrapper(current_url):
        current_url = await _resolve_google_news_wrapper(
            current_url,
            timeout_seconds=max(0.8, min(2.5, timeout_seconds * 0.45)),
        )
    cache_key=hashlib.sha256(
        f"{current_url}{max_redirects}{max_text_chars}{user_agent}".encode("utf-8")
    ).hexdigest()
    cached=_FETCH_CACHE.get(cache_key)
    if not fresh and cached and cached[0] > time.monotonic():
        return copy.deepcopy(cached[1])

    inflight = _FETCH_INFLIGHT.get(cache_key)
    if inflight is not None and not inflight.done():
        return copy.deepcopy(await inflight)

    task = asyncio.create_task(
        _fetch_with_total_budget(
            original_url=original_url,
            current_url=current_url,
            timeout_seconds=timeout_seconds,
            direct_timeout_seconds=direct_timeout,
            max_redirects=max_redirects,
            max_text_chars=max_text_chars,
            user_agent=user_agent,
        )
    )
    _FETCH_INFLIGHT[cache_key] = task
    try:
        result = await task
        _FETCH_CACHE[cache_key]=(
            time.monotonic()+FETCH_CACHE_TTL_SECONDS,
            copy.deepcopy(result),
        )
        return copy.deepcopy(result)
    finally:
        if _FETCH_INFLIGHT.get(cache_key) is task:
            _FETCH_INFLIGHT.pop(cache_key, None)
