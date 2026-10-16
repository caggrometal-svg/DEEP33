from __future__ import annotations

import asyncio
import ipaddress
import math
import re
import socket
import time
from typing import Any, Awaitable, Callable

from pydantic import BaseModel, Field
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


class FetchPolicy(BaseModel):
    base_timeout_seconds: float = Field(default=2.5, ge=0.8, le=8.0)
    min_timeout_seconds: float = Field(default=1.0, ge=0.5, le=4.0)
    max_timeout_seconds: float = Field(default=5.5, ge=1.0, le=8.0)
    global_budget_seconds: float = Field(default=4.8, ge=1.0, le=15.0)
    concurrency: int = Field(default=4, ge=1, le=8)
    target_sources: int = Field(default=3, ge=1, le=5)
    max_sources: int = Field(default=5, ge=1, le=5)
    min_text_chars: int = Field(default=240, ge=80, le=5000)
    min_total_chars: int = Field(default=2400, ge=240, le=50000)
    ewma_alpha: float = Field(default=0.35, ge=0.05, le=0.95)


class DomainFetchProfile(BaseModel):
    samples: int = 0
    successes: int = 0
    failures: int = 0
    timeouts: int = 0
    blocked: int = 0
    ewma_latency_seconds: float = 0.0
    consecutive_timeouts: int = 0


class FetchOutcome(BaseModel):
    url: str
    domain: str = ""
    ok: bool
    status: str
    document: dict[str, Any] | None = None
    latency_ms: float
    timeout_seconds: float = 0.0
    text_chars: int = 0


class FetchBatchResult(BaseModel):
    ok: bool
    status: str
    documents: list[dict[str, Any]] = Field(default_factory=list)
    failures: list[dict[str, Any]] = Field(default_factory=list)
    fetched: int = 0
    failed: int = 0
    cancelled: int = 0
    distinct_domains: int = 0
    total_text_chars: int = 0
    quorum_reached: bool = False
    elapsed_ms: float = 0.0


_DOMAIN_FETCH_PROFILES: dict[str, DomainFetchProfile] = {}


def _host(value: str) -> str:
    return (urlsplit(str(value or "")).hostname or "").casefold().rstrip(".").removeprefix("www.")


def reset_domain_fetch_profiles() -> None:
    _DOMAIN_FETCH_PROFILES.clear()


def domain_fetch_profile(url: str) -> DomainFetchProfile:
    host = _host(url)
    current = _DOMAIN_FETCH_PROFILES.get(host)
    return current.model_copy(deep=True) if current is not None else DomainFetchProfile()


def adaptive_timeout_seconds(url: str, policy: FetchPolicy | None = None) -> float:
    policy = policy or FetchPolicy()
    profile = _DOMAIN_FETCH_PROFILES.get(_host(url))
    if profile is None or profile.samples <= 0:
        return float(policy.base_timeout_seconds)

    # EWMA learns the observed end-to-end source cost, while timeout history adds
    # only a bounded allowance. Repeatedly blocked sources never get a larger
    # budget merely because they reject us.
    latency_target = max(
        policy.min_timeout_seconds,
        profile.ewma_latency_seconds * 1.85 + 0.35,
    )
    timeout_penalty = min(1.0, 0.22 * profile.consecutive_timeouts)
    if profile.blocked > profile.successes:
        timeout_penalty *= 0.35
    return round(
        max(
            policy.min_timeout_seconds,
            min(policy.max_timeout_seconds, latency_target + timeout_penalty),
        ),
        3,
    )


def record_fetch_metric(
    url: str,
    *,
    ok: bool,
    latency_seconds: float,
    status: str,
    policy: FetchPolicy | None = None,
) -> None:
    policy = policy or FetchPolicy()
    host = _host(url)
    if not host:
        return
    profile = _DOMAIN_FETCH_PROFILES.setdefault(host, DomainFetchProfile())
    profile.samples += 1
    if profile.ewma_latency_seconds <= 0:
        profile.ewma_latency_seconds = max(0.001, latency_seconds)
    else:
        profile.ewma_latency_seconds = (
            policy.ewma_alpha * max(0.001, latency_seconds)
            + (1.0 - policy.ewma_alpha) * profile.ewma_latency_seconds
        )
    code = str(status or "").upper()
    if ok:
        profile.successes += 1
        profile.consecutive_timeouts = 0
        return
    profile.failures += 1
    if "TIMEOUT" in code:
        profile.timeouts += 1
        profile.consecutive_timeouts += 1
    else:
        profile.consecutive_timeouts = max(0, profile.consecutive_timeouts - 1)
    if any(marker in code for marker in ("BLOCKED", "HTTP_401", "HTTP_403", "HTTP_429")):
        profile.blocked += 1


FetchSource = Callable[[dict[str, Any], float], Awaitable[dict[str, Any] | None]]


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
    timeout_seconds: float | None = None,
    max_redirects: int = 3,
    max_text_chars: int = 50_000,
    policy: FetchPolicy | None = None,
) -> FetchOutcome:
    policy = policy or FetchPolicy(
        base_timeout_seconds=timeout_seconds or 2.5,
    )
    effective_timeout = (
        max(policy.min_timeout_seconds, min(policy.max_timeout_seconds, timeout_seconds))
        if timeout_seconds is not None
        else adaptive_timeout_seconds(url, policy)
    )
    started = time.perf_counter()
    resolved = str(url)
    try:
        canonical = canonicalize_url(url)
        resolved = await resolve_tracking_url(
            canonical,
            timeout_seconds=min(effective_timeout, 2.5),
            max_redirects=max_redirects,
        )
        document = await asyncio.wait_for(
            fetch_page(
                resolved,
                timeout_seconds=effective_timeout,
                max_redirects=max_redirects,
                max_text_chars=max_text_chars,
                fresh=True,
            ),
            timeout=min(policy.max_timeout_seconds + 0.4, effective_timeout + 0.4),
        )
        text_chars = len(str(document.get("text") or "").strip())
        if text_chars < policy.min_text_chars:
            raise WebFetchError(f"WEB_FETCH_SHORT_TEXT:{text_chars}")
        elapsed = time.perf_counter() - started
        record_fetch_metric(
            resolved,
            ok=True,
            latency_seconds=elapsed,
            status="OK",
            policy=policy,
        )
        return FetchOutcome(
            url=resolved,
            domain=_host(resolved),
            ok=True,
            status="OK",
            document=document,
            latency_ms=round(elapsed * 1000.0, 2),
            timeout_seconds=effective_timeout,
            text_chars=text_chars,
        )
    except asyncio.CancelledError:
        raise
    except asyncio.TimeoutError:
        elapsed = time.perf_counter() - started
        record_fetch_metric(
            resolved,
            ok=False,
            latency_seconds=elapsed,
            status="FETCH_TIMEOUT",
            policy=policy,
        )
        return FetchOutcome(
            url=resolved,
            domain=_host(resolved),
            ok=False,
            status="FETCH_TIMEOUT",
            document=None,
            latency_ms=round(elapsed * 1000.0, 2),
            timeout_seconds=effective_timeout,
        )
    except Exception as exc:
        elapsed = time.perf_counter() - started
        code = (str(exc).strip() or type(exc).__name__)[:120]
        record_fetch_metric(
            resolved,
            ok=False,
            latency_seconds=elapsed,
            status=code,
            policy=policy,
        )
        return FetchOutcome(
            url=resolved,
            domain=_host(resolved),
            ok=False,
            status=code,
            document=None,
            latency_ms=round(elapsed * 1000.0, 2),
            timeout_seconds=effective_timeout,
        )


def _unique_source_candidates(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: list[dict[str, Any]] = []
    seen_domains: set[str] = set()
    seen_urls: set[str] = set()
    for source in sources:
        url = str(source.get("url") or "").strip()
        host = str(source.get("source_domain") or source.get("domain") or _host(url)).casefold().removeprefix("www.")
        if not url or not host or url in seen_urls or host in seen_domains:
            continue
        seen_urls.add(url)
        seen_domains.add(host)
        unique.append(source)
    return unique


async def fetch_source_quorum(
    sources: list[dict[str, Any]],
    fetcher: FetchSource,
    *,
    policy: FetchPolicy | None = None,
) -> FetchBatchResult:
    """Race independent domains and stop once enough high-quality evidence exists."""
    policy = policy or FetchPolicy()
    candidates = _unique_source_candidates(sources)
    if not candidates:
        return FetchBatchResult(ok=False, status="NO_FETCHABLE_SOURCES")

    target = min(policy.target_sources, policy.max_sources, len(candidates))
    started = time.perf_counter()
    deadline = asyncio.get_running_loop().time() + policy.global_budget_seconds
    pending: dict[asyncio.Task, tuple[dict[str, Any], float]] = {}
    next_index = 0
    documents: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    accepted_domains: set[str] = set()
    total_text_chars = 0
    cancelled = 0

    def launch() -> None:
        nonlocal next_index
        while (
            next_index < len(candidates)
            and len(pending) < policy.concurrency
            and len(documents) < policy.max_sources
        ):
            source = candidates[next_index]
            next_index += 1
            url = str(source.get("url") or "")
            timeout = adaptive_timeout_seconds(url, policy)
            task = asyncio.create_task(
                fetcher(source, timeout),
                name=f"fetch-quorum:{next_index}:{_host(url)}",
            )
            pending[task] = (source, timeout)

    launch()
    try:
        while pending and len(documents) < policy.max_sources:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            done, _ = await asyncio.wait(
                tuple(pending),
                timeout=remaining,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                break

            for task in done:
                source, effective_timeout = pending.pop(task)
                url = str(source.get("url") or "")
                source_started = time.perf_counter()
                try:
                    document = task.result()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    status = (str(exc).strip() or f"UNEXPECTED:{type(exc).__name__}")[:120]
                    failures.append({
                        "url": url,
                        "status": status,
                        "timeout_seconds": effective_timeout,
                    })
                    record_fetch_metric(
                        url,
                        ok=False,
                        latency_seconds=max(0.001, time.perf_counter() - source_started),
                        status=status,
                        policy=policy,
                    )
                    continue

                if not isinstance(document, dict):
                    failures.append({
                        "url": url,
                        "status": "FETCH_FAILED",
                        "timeout_seconds": effective_timeout,
                    })
                    record_fetch_metric(
                        url,
                        ok=False,
                        latency_seconds=max(0.001, time.perf_counter() - source_started),
                        status="FETCH_FAILED",
                        policy=policy,
                    )
                    continue

                text = str(document.get("text") or "").strip()
                final_url = str(document.get("url") or document.get("final_url") or url)
                domain = str(document.get("domain") or _host(final_url)).casefold().removeprefix("www.")
                if len(text) < policy.min_text_chars:
                    failures.append({
                        "url": final_url,
                        "status": f"FETCH_SHORT_TEXT:{len(text)}",
                        "timeout_seconds": effective_timeout,
                    })
                    continue
                if not domain or domain in accepted_domains:
                    failures.append({
                        "url": final_url,
                        "status": "FETCH_DUPLICATE_DOMAIN",
                        "timeout_seconds": effective_timeout,
                    })
                    continue

                accepted_domains.add(domain)
                documents.append(document)
                total_text_chars += len(text)
                record_fetch_metric(
                    final_url,
                    ok=True,
                    latency_seconds=max(0.001, time.perf_counter() - source_started),
                    status="OK",
                    policy=policy,
                )

            quality_ready = (
                len(documents) >= target
                and total_text_chars >= policy.min_total_chars
            )
            if quality_ready:
                break
            launch()
    finally:
        for task in pending:
            if not task.done():
                task.cancel()
                cancelled += 1
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    elapsed_ms = round((time.perf_counter() - started) * 1000.0, 2)
    quorum_reached = len(documents) >= target and total_text_chars >= policy.min_total_chars
    if quorum_reached:
        status = "OK"
    elif documents:
        status = "DEGRADED"
    else:
        status = "NO_FETCHABLE_SOURCES"
    return FetchBatchResult(
        ok=bool(documents),
        status=status,
        documents=documents[: policy.max_sources],
        failures=failures,
        fetched=len(documents),
        failed=len(failures),
        cancelled=cancelled,
        distinct_domains=len(accepted_domains),
        total_text_chars=total_text_chars,
        quorum_reached=quorum_reached,
        elapsed_ms=elapsed_ms,
    )


async def fetch_many(
    urls: list[str],
    *,
    timeout_seconds: float = 2.5,
    concurrency: int = 4,
    max_redirects: int = 3,
    max_text_chars: int = 50_000,
    target_sources: int = 3,
    max_sources: int = 5,
    global_budget_seconds: float = 4.8,
    min_text_chars: int = 240,
    min_total_chars: int = 2400,
) -> dict[str, Any]:
    policy = FetchPolicy(
        base_timeout_seconds=timeout_seconds,
        concurrency=concurrency,
        target_sources=target_sources,
        max_sources=max_sources,
        global_budget_seconds=global_budget_seconds,
        min_text_chars=min_text_chars,
        min_total_chars=min_total_chars,
    )
    sources = [
        {"url": url, "source_domain": _host(url)}
        for url in urls
        if str(url or "").strip()
    ]

    async def fetcher(source: dict[str, Any], adaptive_timeout: float) -> dict[str, Any] | None:
        outcome = await fetch_one(
            str(source.get("url") or ""),
            timeout_seconds=adaptive_timeout,
            max_redirects=max_redirects,
            max_text_chars=max_text_chars,
            policy=policy,
        )
        if not outcome.ok or outcome.document is None:
            return None
        return {
            **outcome.document,
            "domain": outcome.domain,
            "text_chars": outcome.text_chars,
            "fetch_latency_ms": outcome.latency_ms,
            "fetch_timeout_seconds": outcome.timeout_seconds,
        }

    result = await fetch_source_quorum(sources, fetcher, policy=policy)
    return result.model_dump()
