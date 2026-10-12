from __future__ import annotations

import asyncio
import ipaddress
import math
import re
import socket
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import PurePosixPath
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import httpx

from tools.web_fetch import WebFetchError, fetch_page, validate_public_url

TRACKING_KEYS = {
    "fbclid", "gclid", "dclid", "msclkid", "mc_cid", "mc_eid",
    "ref", "ref_src", "source", "campaign", "campaign_id",
}
TRACKING_PREFIXES = ("utm_",)
BINARY_EXTENSIONS = {
    ".7z", ".apk", ".avi", ".bin", ".bz2", ".dmg", ".doc", ".docx", ".exe",
    ".gif", ".gz", ".iso", ".jar", ".jpeg", ".jpg", ".m4a", ".mkv", ".mov",
    ".mp3", ".mp4", ".mpeg", ".pdf", ".png", ".ppt", ".pptx", ".rar", ".svg",
    ".tar", ".tgz", ".tif", ".tiff", ".wav", ".webm", ".webp", ".xls", ".xlsx",
    ".zip",
}
SHORTENER_HOSTS = {
    "bit.ly", "buff.ly", "cutt.ly", "goo.gl", "is.gd", "ow.ly", "rebrand.ly",
    "shorturl.at", "t.co", "tiny.cc", "tinyurl.com",
}


@dataclass(slots=True)
class FetchOutcome:
    url: str
    ok: bool
    status: str
    document: dict | None
    latency_ms: float


def canonicalize_url(url: str) -> str:
    candidate = str(url or "").strip()
    validate_public_url(candidate)
    parts = urlsplit(candidate)
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower().rstrip(".")
    port = parts.port
    netloc = host
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{host}:{port}"
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    if path != "/":
        path = path.rstrip("/")
    filtered = []
    for key, value in parse_qsl(parts.query, keep_blank_values=False):
        lowered = key.casefold()
        if lowered in TRACKING_KEYS or any(lowered.startswith(prefix) for prefix in TRACKING_PREFIXES):
            continue
        filtered.append((key, value))
    query = urlencode(filtered, doseq=True)
    normalized = urlunsplit((scheme, netloc, path, query, ""))
    validate_public_url(normalized)
    extension = PurePosixPath(path.casefold()).suffix
    if extension in BINARY_EXTENSIONS:
        raise WebFetchError("WEB_FETCH_BINARY_URL_BLOCKED")
    return normalized


async def resolve_tracking_url(
    url: str,
    *,
    timeout_seconds: float = 2.5,
    max_redirects: int = 3,
) -> str:
    current = canonicalize_url(url)
    host = (urlsplit(current).hostname or "").lower()
    if host not in SHORTENER_HOSTS and not any(k in urlsplit(current).query.casefold() for k in ("url=", "target=", "redirect=")):
        return current

    timeout = httpx.Timeout(connect=1.0, read=1.5, write=1.0, pool=0.5)
    limits = httpx.Limits(max_connections=6, max_keepalive_connections=3)
    headers = {
        "User-Agent": "DEEP33-URLResolver/1.0",
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.1",
    }

    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=False,
        limits=limits,
    ) as client:
        for _ in range(max(1, min(3, int(max_redirects))) + 1):
            validate_public_url(current)
            response: httpx.Response | None = None
            try:
                response = await asyncio.wait_for(
                    client.head(current, headers=headers),
                    timeout=min(2.0, timeout_seconds),
                )
                if response.status_code in {405, 501}:
                    response = await asyncio.wait_for(
                        client.get(current, headers={**headers, "Range": "bytes=0-2047"}),
                        timeout=min(2.0, timeout_seconds),
                    )
            except (asyncio.TimeoutError, httpx.HTTPError):
                return current

            if response.status_code not in {301, 302, 303, 307, 308}:
                return canonicalize_url(str(response.url))
            location = response.headers.get("location")
            if not location:
                return current
            current = canonicalize_url(urljoin(current, location))
    return current


def _tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-záéíóúñ0-9]{2,}", str(text or "").casefold())
        if token not in {"de", "la", "el", "los", "las", "un", "una", "y", "o", "en", "por", "para", "del"}
    }


def _overlap_score(query_tokens: set[str], text: str) -> float:
    if not query_tokens:
        return 0.0
    evidence = _tokens(text)
    if not evidence:
        return 0.0
    return min(1.0, len(query_tokens & evidence) / max(1, len(query_tokens)))


def _freshness_score(item: dict) -> float:
    raw = str(item.get("published_at") or item.get("retrieved_at") or "").strip()
    if not raw:
        return 0.35
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        age_hours = max(
            0.0,
            (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds() / 3600.0,
        )
        return math.exp(-age_hours / (24.0 * 14.0))
    except ValueError:
        return 0.35


def _domain_score(url: str) -> float:
    host = (urlsplit(url).hostname or "").casefold()
    if not host:
        return 0.0
    if host.endswith((".gov", ".gov.cl", ".gob.cl", ".edu", ".edu.cl", ".int")):
        return 1.0
    if host.endswith((".org", ".org.cl")):
        return 0.8
    return 0.6


def score_result(query: str, item: dict) -> float:
    q = _tokens(query)
    title_score = _overlap_score(q, item.get("title", ""))
    snippet_score = _overlap_score(q, item.get("snippet", "") or item.get("text", ""))
    domain_score = _domain_score(str(item.get("url") or item.get("final_url") or ""))
    freshness_score = _freshness_score(item)
    query_match = _overlap_score(
        q,
        " ".join(
            [
                str(item.get("title") or ""),
                str(item.get("snippet") or ""),
                str(item.get("text") or "")[:2000],
            ]
        ),
    )

    penalties = 0.0
    text = " ".join(
        [
            str(item.get("title") or ""),
            str(item.get("snippet") or ""),
            str(item.get("text") or "")[:500],
        ]
    ).casefold()
    if any(marker in text for marker in ("access denied", "enable javascript", "captcha", "verify you are human")):
        penalties += 0.35
    url = str(item.get("url") or item.get("final_url") or "")
    if not url:
        penalties += 0.50

    score = (
        0.30 * title_score
        + 0.25 * snippet_score
        + 0.20 * domain_score
        + 0.15 * freshness_score
        + 0.10 * query_match
        - penalties
    )
    return round(max(0.0, min(1.0, score)), 6)


def rank_evidence(query: str, items: list[dict]) -> list[dict]:
    ranked = [{**item, "score": score_result(query, item)} for item in items]
    ranked.sort(key=lambda item: (item["score"], str(item.get("title") or "")), reverse=True)
    return ranked


async def fetch_one(
    url: str,
    *,
    timeout_seconds: float = 2.5,
    max_redirects: int = 3,
    max_text_chars: int = 50_000,
) -> FetchOutcome:
    started = time.perf_counter()
    try:
        canonical = canonicalize_url(url)
        resolved = await resolve_tracking_url(
            canonical,
            timeout_seconds=timeout_seconds,
            max_redirects=max_redirects,
        )
        document = await asyncio.wait_for(
            fetch_page(
                resolved,
                timeout_seconds=min(2.5, timeout_seconds),
                max_redirects=max_redirects,
                max_text_chars=max_text_chars,
                fresh=True,
            ),
            timeout=min(3.0, timeout_seconds + 0.5),
        )
        return FetchOutcome(
            url=resolved,
            ok=True,
            status="OK",
            document=document,
            latency_ms=round((time.perf_counter() - started) * 1000.0, 2),
        )
    except asyncio.TimeoutError:
        return FetchOutcome(
            url=str(url),
            ok=False,
            status="FETCH_TIMEOUT",
            document=None,
            latency_ms=round((time.perf_counter() - started) * 1000.0, 2),
        )
    except Exception as exc:
        code = str(exc).strip() or type(exc).__name__
        return FetchOutcome(
            url=str(url),
            ok=False,
            status=code[:120],
            document=None,
            latency_ms=round((time.perf_counter() - started) * 1000.0, 2),
        )


async def fetch_many(
    urls: list[str],
    *,
    timeout_seconds: float = 2.5,
    concurrency: int = 4,
    max_redirects: int = 3,
    max_text_chars: int = 50_000,
) -> dict:
    semaphore = asyncio.Semaphore(max(1, min(8, int(concurrency))))

    async def guarded(url: str) -> FetchOutcome:
        async with semaphore:
            return await fetch_one(
                url,
                timeout_seconds=timeout_seconds,
                max_redirects=max_redirects,
                max_text_chars=max_text_chars,
            )

    tasks = [
        asyncio.create_task(guarded(url), name=f"fetch:{index}")
        for index, url in enumerate(urls)
    ]
    outcomes: list[FetchOutcome] = []
    try:
        for task in asyncio.as_completed(tasks):
            try:
                outcomes.append(await task)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                outcomes.append(
                    FetchOutcome(
                        url="",
                        ok=False,
                        status=f"UNEXPECTED:{type(exc).__name__}",
                        document=None,
                        latency_ms=0.0,
                    )
                )
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    documents = [outcome.document for outcome in outcomes if outcome.ok and outcome.document]
    failures = [
        {"url": outcome.url, "status": outcome.status, "latency_ms": outcome.latency_ms}
        for outcome in outcomes
        if not outcome.ok
    ]
    return {
        "ok": bool(documents),
        "status": "OK" if documents else "NO_FETCHABLE_SOURCES",
        "documents": documents,
        "failures": failures,
        "fetched": len(documents),
        "failed": len(failures),
    }
