from __future__ import annotations

import asyncio
import copy
import hashlib
import html
import json
import ipaddress
import os
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
ALLOWED_CONTENT_TYPES={"text/html","application/xhtml+xml","text/plain"}
BLOCKED_HTML_TAGS={"script","style","noscript","svg","canvas","template"}

class WebFetchError(RuntimeError): pass
class SSRFBlockedError(WebFetchError): pass
class DownloadLimitError(WebFetchError): pass

class HTMLTextExtractor(HTMLParser):
    """Extract visible HTML and conservative, server-rendered article metadata."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title_parts: list[str] = []
        self.meta_titles: dict[str, str] = {}
        self.meta_descriptions: dict[str, str] = {}
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


def _structured_article_fields(blocks: list[str]) -> tuple[str, str, str]:
    """Return conservative articleBody, description, and headline JSON-LD candidates."""
    article_bodies: list[tuple[int, str]] = []
    descriptions: list[tuple[int, str]] = []
    headlines: list[tuple[int, str]] = []

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

        for key, child in value.items():
            if key not in {"articleBody", "description", "headline", "name"}:
                visit(child)

    for block in blocks:
        try:
            visit(json.loads(block))
        except (TypeError, ValueError):
            continue

    article_bodies.sort(key=lambda item: (item[0], -len(item[1])))
    descriptions.sort(key=lambda item: (item[0], -len(item[1])))
    headlines.sort(key=lambda item: (item[0], -len(item[1])))
    return (
        article_bodies[0][1] if article_bodies else "",
        descriptions[0][1] if descriptions else "",
        headlines[0][1] if headlines else "",
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
        return "", plain[:max_text_chars]

    parser = HTMLTextExtractor()
    parser.feed(decoded)
    parser.close()
    visible_text = parser.text[:max_text_chars].strip()
    article_body, structured_description, structured_title = _structured_article_fields(
        parser.jsonld_blocks
    )
    title = (parser.title or parser.meta_title or structured_title).strip()[:500]

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


async def _fetch_page_uncached(
    *,
    original_url: str,
    current_url: str,
    timeout_seconds: float,
    max_redirects: int,
    max_text_chars: int,
    user_agent: str,
) -> dict:
    timeout=httpx.Timeout(
        connect=min(timeout_seconds, 1.0),
        read=min(timeout_seconds, 1.5),
        write=min(timeout_seconds, 1.0),
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
            title, text = _extract_text(content, content_type, max_text_chars)
            text = text.strip()
            if len(text) < 20: raise WebFetchError("WEB_FETCH_EMPTY_TEXT")
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
            }
        except httpx.TimeoutException as exc:
            raise WebFetchError("WEB_FETCH_TIMEOUT") from exc
        except httpx.HTTPError as exc:
            raise WebFetchError("WEB_FETCH_NETWORK_ERROR") from exc
    raise WebFetchError("WEB_FETCH_REDIRECT_LIMIT")


async def fetch_page(url,*,timeout_seconds=DEFAULT_TIMEOUT_SECONDS,max_redirects=MAX_REDIRECTS,max_text_chars=MAX_TEXT_CHARS,user_agent=DEFAULT_USER_AGENT,fresh=False):
    if not 1<=max_redirects<=MAX_REDIRECTS: raise ValueError("max_redirects must be between 1 and 3")
    timeout_seconds=max(1.0,min(8.0,timeout_seconds)); max_text_chars=max(1000,min(MAX_TEXT_CHARS,max_text_chars))
    original_url=url.strip(); current_url=await asyncio.to_thread(validate_public_url, original_url)
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
        _fetch_page_uncached(
            original_url=original_url,
            current_url=current_url,
            timeout_seconds=timeout_seconds,
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
