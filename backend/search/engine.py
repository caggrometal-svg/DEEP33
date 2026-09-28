from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlparse

TOKEN_RE = re.compile(r"[\\wáéíóúüñÁÉÍÓÚÜÑ]{2,}", re.UNICODE)
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


@dataclass(frozen=True)
class SearchPlan:
    original_query: str
    queries: list[str]
    depth: str


def _tokens(text: str) -> list[str]:
    return [token.lower() for token in TOKEN_RE.findall(text) if token.lower() not in _STOPWORDS]


def _canonical_url(url: str) -> str:
    parsed = urlparse(url.strip())
    path = parsed.path.rstrip("/") or "/"
    return parsed._replace(fragment="", path=path).geturl()


def _domain_quality(url: str) -> float:
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    if not host:
        return 0.0
    if host.endswith(".gov") or ".gov." in host:
        return 1.0
    if host.endswith(".edu") or ".edu." in host:
        return 0.95
    if host.endswith(".org"):
        return 0.8
    return 0.55


def _text_score(query_tokens: list[str], item: dict) -> float:
    title = str(item.get("title") or "").lower()
    snippet = str(item.get("snippet") or "").lower()
    if not query_tokens:
        return 0.0
    title_hits = sum(1 for token in query_tokens if token in title)
    snippet_hits = sum(1 for token in query_tokens if token in snippet)
    return (title_hits / len(query_tokens)) * 0.70 + (snippet_hits / len(query_tokens)) * 0.30


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
    ranked = []
    for item in results:
        url = str(item.get("url") or "").strip()
        if not url:
            continue
        relevance = _text_score(query_tokens, item)
        quality = _domain_quality(url)
        freshness = _freshness_score(item)
        score = relevance * 0.65 + quality * 0.20 + freshness * 0.15
        ranked.append({**item, "score": round(score, 6)})
    ranked.sort(
        key=lambda item: (item.get("score", 0.0), str(item.get("title", "")).lower()),
        reverse=True,
    )
    return ranked


def deduplicate(results: list[dict], limit: int) -> list[dict]:
    seen_urls = set()
    seen_titles = set()
    output = []
    for item in results:
        url = _canonical_url(str(item.get("url") or ""))
        title = " ".join(str(item.get("title") or "").lower().split())
        if not url or url in seen_urls:
            continue
        if title and title in seen_titles:
            continue
        seen_urls.add(url)
        if title:
            seen_titles.add(title)
        output.append({**item, "url": url})
        if len(output) >= limit:
            break
    return output


class SearchEngine:
    """DEEP33 orchestration layer above concrete web-search providers."""

    def __init__(self, *, max_results: int = 5, max_queries: int = 3) -> None:
        self.max_results = max(1, min(8, int(max_results)))
        self.max_queries = max(1, min(3, int(max_queries)))

    def plan(self, query: str) -> SearchPlan:
        cleaned = " ".join(query.split()).strip()
        lowered = cleaned.lower()
        exact_deep = bool(re.search(r"\\bdeep\\b", lowered))
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

    async def search(
        self,
        query: str,
        *,
        provider: str,
        api_key: str,
        timeout_seconds: float,
        fallback_ddg: bool,
    ) -> dict:
        plan = self.plan(query)
        provider_name = provider.strip().lower() or "auto"
        bing_enabled = os.getenv("WEB_SEARCH_BING_ENABLED", "true").strip().lower() == "true"

        if provider_name == "tavily":
            providers = ["tavily"]
            if bing_enabled:
                providers.append("bing")
            if fallback_ddg:
                providers.append("duckduckgo")
        elif provider_name == "bing":
            providers = ["bing"] + (["duckduckgo"] if fallback_ddg else [])
        elif provider_name == "duckduckgo":
            providers = ["duckduckgo"]
        else:
            providers = []
            if api_key:
                providers.append("tavily")
            if bing_enabled:
                providers.append("bing")
            if fallback_ddg or not providers:
                providers.append("duckduckgo")

        successful = []
        errors = []
        parallel = os.getenv("WEB_SEARCH_PARALLEL_PROVIDERS", "false").strip().lower() == "true"

        for planned_query in plan.queries:
            if parallel and len(providers) > 1:
                results = await asyncio.gather(
                    *(
                        self._provider_search(
                            name,
                            planned_query,
                            timeout_seconds=timeout_seconds,
                            api_key=api_key,
                        )
                        for name in providers
                    ),
                    return_exceptions=True,
                )
                for name, result in zip(providers, results):
                    if isinstance(result, Exception):
                        errors.append(f"{name}:{type(result).__name__}")
                    elif result:
                        successful.append((planned_query, name, result))
            else:
                for name in providers:
                    try:
                        result = await self._provider_search(
                            name,
                            planned_query,
                            timeout_seconds=timeout_seconds,
                            api_key=api_key,
                        )
                        if result:
                            successful.append((planned_query, name, result))
                            break
                    except Exception as exc:
                        errors.append(f"{name}:{type(exc).__name__}")

        merged = []
        providers_used = []
        for planned_query, name, items in successful:
            if name not in providers_used:
                providers_used.append(name)
            for item in items:
                merged.append(
                    {
                        **item,
                        "search_query": planned_query,
                        "provider": name,
                    }
                )

        ranked = rank_results(plan.original_query, merged)
        selected = deduplicate(ranked, self.max_results)
        return {
            "ok": bool(selected),
            "engine": "DEEP33 Search Engine",
            "engine_version": "1.0.0",
            "query": plan.original_query,
            "depth": plan.depth,
            "queries": plan.queries,
            "providers": providers_used,
            "provider": providers_used[0] if len(providers_used) == 1 else "multi" if providers_used else None,
            "results": selected,
            "sources": selected,
            "errors": errors[:8],
        }
