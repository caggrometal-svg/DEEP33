from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlparse


ENGINE_VERSION = "1.1.0"
TOKEN_RE = re.compile(r"[\wáéíóúüñÁÉÍÓÚÜÑ]{2,}", re.UNICODE)
DEEP_TERMS = (
    "investiga", "investigar", "investigación", "analiza", "analizar",
    "compara", "contrasta", "verifica", "verificar", "evidencia",
    "fuentes", "en profundidad", "deep", "research", "fact check",
    "fact-check",
)
QUERY_VARIANTS = ("fuente oficial", "noticias recientes")

_STOPWORDS = {
    "para", "como", "que", "qué", "una", "uno", "los", "las", "del", "con",
    "por", "en", "sobre", "the", "and", "for", "with", "from", "this", "that",
}
_TRUSTED_SUFFIXES = {".gov": 1.0, ".edu": 0.95, ".org": 0.80}
MIN_FALLBACK_RESULTS = max(1, min(5, int(os.getenv("WEB_SEARCH_MIN_FALLBACK_RESULTS", "3"))))
PROVIDER_RETRIES = max(0, min(2, int(os.getenv("WEB_SEARCH_PROVIDER_RETRIES", "1"))))
RETRY_BACKOFF_SECONDS = max(0.0, min(2.0, float(os.getenv("WEB_SEARCH_RETRY_BACKOFF_SECONDS", "0.35"))))
PARALLEL_PROVIDERS = os.getenv("WEB_SEARCH_PARALLEL_PROVIDERS", "true").strip().lower() == "true"
PARALLEL_QUERIES = os.getenv("WEB_SEARCH_PARALLEL_QUERIES", "true").strip().lower() == "true"


@dataclass(frozen=True)
class SearchPlan:
    original_query: str
    queries: list[str]
    depth: str


def _tokens(text: str) -> list[str]:
    return [
        token.lower()
        for token in TOKEN_RE.findall(text)
        if token.lower() not in _STOPWORDS
    ]


def _canonical_url(url: str) -> str:
    parsed = urlparse(url.strip())
    path = parsed.path.rstrip("/") or "/"
    return parsed._replace(fragment="", path=path).geturl()


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower().rstrip(".")


def _domain_quality(url: str) -> float:
    host = _host(url)
    if not host:
        return 0.0
    exact = {
        "reuters.com": 0.95,
        "apnews.com": 0.95,
        "bbc.com": 0.90,
        "bbc.co.uk": 0.90,
        "nasa.gov": 1.0,
        "who.int": 1.0,
        "un.org": 1.0,
    }
    if host in exact:
        return exact[host]
    for suffix, score in _TRUSTED_SUFFIXES.items():
        if host.endswith(suffix) or f"{suffix}." in host:
            return score
    return 0.55


def _evidence_text(item: dict) -> str:
    return f'{item.get("title", "")} {item.get("snippet", "")}'.lower()


def _similarity(left: dict, right: dict) -> float:
    a = set(_tokens(_evidence_text(left)))
    b = set(_tokens(_evidence_text(right)))
    if not a or not b:
        return 0.0
    return len(a & b) / max(1, len(a | b))


def _corroboration_counts(results: list[dict]) -> dict[int, int]:
    counts = {id(item): 0 for item in results}
    for index, left in enumerate(results):
        left_host = _host(str(left.get("url") or ""))
        for right in results[index + 1:]:
            right_host = _host(str(right.get("url") or ""))
            if not left_host or not right_host or left_host == right_host:
                continue
            if _similarity(left, right) >= 0.25:
                counts[id(left)] += 1
                counts[id(right)] += 1
    return counts


def _freshness_score(item: dict) -> float:
    value = str(item.get("published_at") or "").strip()
    if not value:
        return 0.0
    try:
        published = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        age_days = max(
            0.0,
            (datetime.now(timezone.utc) - published.astimezone(timezone.utc)).total_seconds() / 86400,
        )
        return max(0.0, 1.0 - min(age_days / 365.0, 1.0))
    except ValueError:
        return 0.0


def rank_results(query: str, results: list[dict]) -> list[dict]:
    query_tokens = _tokens(query)
    support = _corroboration_counts(results)
    ranked = []
    for item in results:
        url = str(item.get("url") or "").strip()
        if not url:
            continue
        relevance = _text_score(query_tokens, item)
        quality = _domain_quality(url)
        freshness = _freshness_score(item)
        corroboration = min(1.0, support.get(id(item), 0) / 2.0)
        score = relevance * 0.50 + quality * 0.20 + freshness * 0.10 + corroboration * 0.20
        ranked.append({
            **item,
            "score": round(score, 6),
            "corroboration_count": support.get(id(item), 0),
            "corroborated": support.get(id(item), 0) >= 1,
            "source_domain": _host(url),
        })
    ranked.sort(
        key=lambda item: (
            item.get("score", 0.0),
            item.get("corroboration_count", 0),
            str(item.get("title", "")).lower(),
        ),
        reverse=True,
    )
    return ranked


def _text_score(query_tokens: list[str], item: dict) -> float:
    title = str(item.get("title") or "").lower()
    snippet = str(item.get("snippet") or "").lower()
    if not query_tokens:
        return 0.0
    title_hits = sum(1 for token in query_tokens if token in title)
    snippet_hits = sum(1 for token in query_tokens if token in snippet)
    return (title_hits / len(query_tokens)) * 0.70 + (snippet_hits / len(query_tokens)) * 0.30


def deduplicate(results: list[dict], limit: int) -> list[dict]:
    seen_urls = set()
    seen_title_domain = set()
    seen_domains = set()
    output = []

    # Pass 1: guarantee source-domain diversity where possible. Identical titles
    # from different domains are retained because they are evidence of corroboration.
    for item in results:
        url = _canonical_url(str(item.get("url") or ""))
        title = " ".join(str(item.get("title") or "").lower().split())
        domain = _host(url)
        title_key = (domain, title) if title else None
        if not url or url in seen_urls or (title_key and title_key in seen_title_domain):
            continue
        if domain and domain in seen_domains:
            continue
        seen_urls.add(url)
        if title_key:
            seen_title_domain.add(title_key)
        if domain:
            seen_domains.add(domain)
        output.append({**item, "url": url})
        if len(output) >= limit:
            return output

    # Pass 2: fill remaining slots, capped by two results per domain.
    domain_counts: dict[str, int] = {}
    for item in output:
        domain = _host(item["url"])
        domain_counts[domain] = domain_counts.get(domain, 0) + 1

    for item in results:
        url = _canonical_url(str(item.get("url") or ""))
        title = " ".join(str(item.get("title") or "").lower().split())
        domain = _host(url)
        title_key = (domain, title) if title else None
        if not url or url in seen_urls or (title_key and title_key in seen_title_domain):
            continue
        if domain and domain_counts.get(domain, 0) >= 2:
            continue
        seen_urls.add(url)
        if title_key:
            seen_title_domain.add(title_key)
        domain_counts[domain] = domain_counts.get(domain, 0) + 1
        output.append({**item, "url": url})
        if len(output) >= limit:
            break
    return output


class SearchEngine:
    """Provider-agnostic DEEP33 web-search orchestration layer."""

    def __init__(self, *, max_results: int = 5, max_queries: int = 3) -> None:
        self.max_results = max(1, min(8, int(max_results)))
        self.max_queries = max(1, min(3, int(max_queries)))

    def plan(self, query: str) -> SearchPlan:
        cleaned = " ".join(query.split()).strip()
        lowered = cleaned.lower()
        exact_deep = bool(re.search(r"\bdeep\b", lowered))
        deep_signal = exact_deep or any(
            term != "deep" and term in lowered for term in DEEP_TERMS
        )
        depth = "deep" if deep_signal else "standard"
        queries = [cleaned]
        if depth == "deep":
            for variant in QUERY_VARIANTS:
                candidate = f"{cleaned} {variant}".strip()
                if candidate.lower() != cleaned.lower() and len(queries) < self.max_queries:
                    queries.append(candidate)
        return SearchPlan(original_query=cleaned, queries=queries, depth=depth)

    async def _provider_search(
        self,
        provider: str,
        query: str,
        *,
        timeout_seconds: float,
        api_key: str,
    ) -> list[dict]:
        from tools.web_search import _bing_search, _duckduckgo_search, _tavily_search

        if provider == "tavily":
            if not api_key:
                return []
            return await _tavily_search(query, api_key, timeout_seconds, self.max_results)
        if provider == "bing":
            return await _bing_search(query, timeout_seconds, self.max_results)
        return await _duckduckgo_search(query, timeout_seconds, self.max_results)

    async def _provider_search_with_retry(
        self,
        provider: str,
        query: str,
        *,
        timeout_seconds: float,
        api_key: str,
        fast_mode: bool = False,
    ) -> tuple[list[dict], list[str]]:
        errors: list[str] = []
        max_attempts = 1 if fast_mode else PROVIDER_RETRIES + 1
        for attempt in range(max_attempts):
            try:
                result = await asyncio.wait_for(
                    self._provider_search(
                        provider,
                        query,
                        timeout_seconds=timeout_seconds,
                        api_key=api_key,
                    ),
                    timeout=max(1.0, float(timeout_seconds) + 0.5),
                )
                return result, errors
            except Exception as exc:
                errors.append(f"{provider}:{type(exc).__name__}:attempt={attempt + 1}")
                if (
                    not fast_mode
                    and attempt < max_attempts - 1
                    and RETRY_BACKOFF_SECONDS
                ):
                    await asyncio.sleep(RETRY_BACKOFF_SECONDS * (attempt + 1))
        return [], errors

    def _providers(self, provider_name: str, api_key: str, fallback_ddg: bool) -> list[str]:
        bing_enabled = os.getenv("WEB_SEARCH_BING_ENABLED", "true").strip().lower() == "true"
        if provider_name == "tavily":
            providers = ["tavily"]
            if bing_enabled:
                providers.append("bing")
            if fallback_ddg:
                providers.append("duckduckgo")
            return providers
        if provider_name == "bing":
            return ["bing"] + (["duckduckgo"] if fallback_ddg else [])
        if provider_name == "duckduckgo":
            return ["duckduckgo"]
        providers: list[str] = []
        if api_key:
            providers.append("tavily")
        if bing_enabled:
            providers.append("bing")
        if fallback_ddg or not providers:
            providers.append("duckduckgo")
        return providers

    async def _run_query(
        self,
        providers: list[str],
        planned_query: str,
        *,
        plan_depth: str,
        timeout_seconds: float,
        api_key: str,
        fast_mode: bool = False,
    ) -> tuple[list[tuple[str, list[dict]]], list[str], list[str]]:
        successful: list[tuple[str, list[dict]]] = []
        errors: list[str] = []
        attempted: list[str] = []

        async def call(name: str) -> tuple[str, list[dict], list[str]]:
            results, provider_errors = await self._provider_search_with_retry(
                name,
                planned_query,
                timeout_seconds=timeout_seconds,
                api_key=api_key,
                fast_mode=fast_mode,
            )
            return name, results, provider_errors

        if fast_mode and len(providers) > 1:
            # Race providers and return as soon as one has enough usable results.
            # Fast mode deliberately omits retries and deep query fan-out.
            required_results = min(self.max_results, MIN_FALLBACK_RESULTS)
            tasks = [asyncio.create_task(call(name)) for name in providers]
            try:
                for task in asyncio.as_completed(tasks):
                    name, results, provider_errors = await task
                    attempted.append(name)
                    errors.extend(provider_errors)
                    if results:
                        successful.append((name, results))
                        if len(results) >= required_results:
                            for other in tasks:
                                if not other.done():
                                    other.cancel()
                            break
            finally:
                await asyncio.gather(*tasks, return_exceptions=True)
            return successful, errors, attempted

        if fast_mode:
            name, results, provider_errors = await call(providers[0]) if providers else ("", [], [])
            if name:
                attempted.append(name)
                errors.extend(provider_errors)
                if results:
                    successful.append((name, results))
            return successful, errors, attempted

        if plan_depth == "deep":
            gathered = await asyncio.gather(*(call(name) for name in providers))
            for name, results, provider_errors in gathered:
                attempted.append(name)
                errors.extend(provider_errors)
                if results:
                    successful.append((name, results))
            return successful, errors, attempted

        if PARALLEL_PROVIDERS and len(providers) > 1:
            tasks = [asyncio.create_task(call(name)) for name in providers]
            try:
                for task in asyncio.as_completed(tasks):
                    name, results, provider_errors = await task
                    attempted.append(name)
                    errors.extend(provider_errors)
                    if results:
                        successful.append((name, results))
                        if len(results) >= MIN_FALLBACK_RESULTS:
                            for other in tasks:
                                if not other.done():
                                    other.cancel()
                            break
            finally:
                await asyncio.gather(*tasks, return_exceptions=True)
            return successful, errors, attempted

        for name in providers:
            attempted.append(name)
            results, provider_errors = await self._provider_search_with_retry(
                name,
                planned_query,
                timeout_seconds=timeout_seconds,
                api_key=api_key,
            )
            errors.extend(provider_errors)
            if results:
                successful.append((name, results))
                if len(results) >= MIN_FALLBACK_RESULTS:
                    break
        return successful, errors, attempted

    async def search(
        self,
        query: str,
        *,
        provider: str,
        api_key: str,
        timeout_seconds: float,
        fallback_ddg: bool,
        fast_mode: bool = False,
    ) -> dict:
        plan = self.plan(query)
        provider_name = provider.strip().lower() or "auto"
        providers = self._providers(provider_name, api_key, fallback_ddg)

        successful: list[tuple[str, str, list[dict]]] = []
        errors: list[str] = []
        providers_attempted: list[str] = []

        planned_queries = plan.queries[:1] if fast_mode else plan.queries

        async def run_planned(planned_query: str):
            return planned_query, await self._run_query(
                providers,
                planned_query,
                plan_depth=plan.depth,
                timeout_seconds=timeout_seconds,
                api_key=api_key,
                fast_mode=fast_mode,
            )

        if (
            not fast_mode
            and PARALLEL_QUERIES
            and plan.depth == "deep"
            and len(planned_queries) > 1
        ):
            planned_batches = await asyncio.gather(*(run_planned(q) for q in planned_queries))
        else:
            planned_batches = []
            for q in planned_queries:
                planned_batches.append(await run_planned(q))

        for planned_query, (batch, batch_errors, attempted) in planned_batches:
            errors.extend(batch_errors)
            for provider_used in attempted:
                if provider_used not in providers_attempted:
                    providers_attempted.append(provider_used)
            for provider_used, items in batch:
                successful.append((planned_query, provider_used, items))

        executed_queries = list(planned_queries)
        if not successful:
            tokens = _tokens(plan.original_query)
            rescue = " ".join(tokens[:-1]).strip() if len(tokens) >= 3 else ""
            if rescue and rescue.lower() != plan.original_query.lower():
                executed_queries.append(rescue)
                batch, batch_errors, attempted = await self._run_query(
                    providers,
                    rescue,
                    plan_depth="standard",
                    timeout_seconds=timeout_seconds,
                    api_key=api_key,
                    fast_mode=fast_mode,
                )
                errors.extend(batch_errors)
                for provider_used in attempted:
                    if provider_used not in providers_attempted:
                        providers_attempted.append(provider_used)
                for provider_used, items in batch:
                    successful.append((rescue, provider_used, items))

        merged = []
        providers_used = []
        for planned_query, name, items in successful:
            if name not in providers_used:
                providers_used.append(name)
            for item in items:
                merged.append({
                    **item,
                    "search_query": planned_query,
                    "provider": name,
                })

        ranked = rank_results(plan.original_query, merged)
        selected = deduplicate(ranked, self.max_results)
        distinct_domains = sorted({
            _host(str(item.get("url") or ""))
            for item in selected
            if _host(str(item.get("url") or ""))
        })
        corroborated = sum(1 for item in selected if item.get("corroborated"))
        if corroborated:
            verification_level = "corroborated"
        elif len(distinct_domains) >= 2:
            verification_level = "multi-source"
        elif selected:
            verification_level = "single-source"
        else:
            verification_level = "none"

        return {
            "ok": bool(selected),
            "engine": "DEEP33 Search Engine",
            "engine_version": ENGINE_VERSION,
            "provider_independent": True,
            "query": plan.original_query,
            "depth": plan.depth,
            "queries": executed_queries,
            "providers_attempted": providers_attempted,
            "providers": providers_used,
            "provider": providers_used[0] if len(providers_used) == 1 else "multi" if providers_used else None,
            "results": selected,
            "sources": selected,
            "verification": {
                "level": verification_level,
                "distinct_domains": len(distinct_domains),
                "distinct_providers": len(providers_used),
                "corroborated_results": corroborated,
            },
            "errors": errors[:8],
        }
