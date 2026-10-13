from __future__ import annotations

import asyncio
import copy
import hashlib
import logging
import time
from datetime import datetime, timezone
from urllib.parse import urlparse
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from backend.search.engine import _tokens, _topic_tokens, deduplicate, is_realtime_query, rank_results
from tools.web_search import (
    _bing_search,
    _cl0q_search,
    _duckduckgo_search,
    _google_news_search,
    _google_search,
    _mojeek_search,
    _yahoo_search,
)

logger = logging.getLogger(__name__)

PROVIDER_TOTAL_SECONDS = 2.5
GLOBAL_BUDGET_SECONDS = 5.0
FALLBACK_DELAY_SECONDS = 0.35
STANDARD_CORROBORATION_WINDOW_SECONDS = 0.20
CIRCUIT_FAILURE_THRESHOLD = 5
CIRCUIT_OPEN_SECONDS = 60.0
CACHE_TTL_SECONDS = 60.0
CACHE_MAX_ENTRIES = 256


@dataclass(slots=True)
class CircuitState:
    consecutive_failures: int = 0
    opened_until: float = 0.0

    def is_open(self, now: float) -> bool:
        if self.opened_until <= 0.0:
            return False
        if now >= self.opened_until:
            self.consecutive_failures = 0
            self.opened_until = 0.0
            return False
        return True

    def success(self) -> None:
        self.consecutive_failures = 0
        self.opened_until = 0.0

    def failure(self, now: float) -> None:
        self.consecutive_failures += 1
        if self.consecutive_failures >= CIRCUIT_FAILURE_THRESHOLD:
            self.opened_until = now + CIRCUIT_OPEN_SECONDS


@dataclass(slots=True)
class ProviderOutcome:
    provider: str
    ok: bool
    results: list[dict] = field(default_factory=list)
    error_code: str | None = None
    latency_ms: float = 0.0


class TTLRUCache:
    def __init__(self, max_entries: int = CACHE_MAX_ENTRIES) -> None:
        self.max_entries = max(8, int(max_entries))
        self._items: OrderedDict[str, tuple[float, dict]] = OrderedDict()

    def get(self, key: str) -> dict | None:
        now = time.monotonic()
        item = self._items.get(key)
        if item is None:
            return None
        expires_at, value = item
        if expires_at <= now:
            self._items.pop(key, None)
            return None
        self._items.move_to_end(key)
        return copy.deepcopy(value)

    def put(self, key: str, value: dict, ttl_seconds: float) -> None:
        ttl = max(30.0, min(120.0, float(ttl_seconds)))
        self._items[key] = (time.monotonic() + ttl, copy.deepcopy(value))
        self._items.move_to_end(key)
        while len(self._items) > self.max_entries:
            self._items.popitem(last=False)


class WebOrchestrator:
    """Strict-budget parallel search with circuit breaking and graceful fallback."""

    def __init__(
        self,
        *,
        max_results: int = 5,
        provider_total_seconds: float = PROVIDER_TOTAL_SECONDS,
        global_budget_seconds: float = GLOBAL_BUDGET_SECONDS,
        fallback_delay_seconds: float = FALLBACK_DELAY_SECONDS,
        cache_ttl_seconds: float = CACHE_TTL_SECONDS,
    ) -> None:
        self.max_results = max(1, min(8, int(max_results)))
        # Permit short deterministic test budgets while keeping production caps.
        self.provider_total_seconds = max(0.05, min(2.5, float(provider_total_seconds)))
        self.global_budget_seconds = max(0.05, min(5.0, float(global_budget_seconds)))
        self.fallback_delay_seconds = max(0.0, min(2.0, float(fallback_delay_seconds)))
        self.cache_ttl_seconds = max(30.0, min(120.0, float(cache_ttl_seconds)))
        self.cache = TTLRUCache()
        self.circuits: dict[str, CircuitState] = {
            "bing": CircuitState(),
            "google": CircuitState(),
            "google_news": CircuitState(),
            "cl0q": CircuitState(),
            "mojeek": CircuitState(),
            "duckduckgo": CircuitState(),
            "yahoo": CircuitState(),
        }
        self.providers: dict[str, Callable[[str, float, int], Awaitable[list[dict]]]] = {
            "bing": self._bing,
            "google": self._google,
            "google_news": self._google_news,
            "cl0q": self._cl0q,
            "mojeek": self._mojeek,
            "duckduckgo": self._duckduckgo,
        }
        # Yahoo is a last-resort general-web fallback, not part of every request fan-out.
        self.fallback_providers: dict[str, Callable[[str, float, int], Awaitable[list[dict]]]] = {
            "yahoo": self._yahoo,
        }

    async def _bing(self, query: str, timeout: float, limit: int) -> list[dict]:
        # The orchestrator ranks once after merging every provider's candidates.
        return await _bing_search(query, timeout, limit, preserve_candidates=True)

    async def _mojeek(self, query: str, timeout: float, limit: int) -> list[dict]:
        return await _mojeek_search(query, timeout, limit)

    async def _cl0q(self, query: str, timeout: float, limit: int) -> list[dict]:
        return await _cl0q_search(query, timeout, limit)

    async def _google(self, query: str, timeout: float, limit: int) -> list[dict]:
        # Direct adapter callers keep strict legacy behavior; orchestration keeps
        # candidates so independent results can be ranked against the original query.
        return await _google_search(query, timeout, limit, preserve_candidates=True)

    async def _google_news(self, query: str, timeout: float, limit: int) -> list[dict]:
        # RSS news fallback is independent of general-search anti-bot HTML.
        # Non-news queries return immediately without a network request.
        return await _google_news_search(query, timeout, limit)

    async def _duckduckgo(self, query: str, timeout: float, limit: int) -> list[dict]:
        return await _duckduckgo_search(query, timeout, limit)

    async def _yahoo(self, query: str, timeout: float, limit: int) -> list[dict]:
        return await _yahoo_search(query, timeout, limit, preserve_candidates=True)

    def _cache_key(self, query: str) -> str:
        normalized = " ".join(query.split()).strip().casefold()
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    @staticmethod
    def _provider_query(query: str) -> str:
        """Reduce provider-side noise without changing the user's ranking query."""
        cleaned = " ".join(str(query or "").split()).strip()
        if not cleaned or is_realtime_query(cleaned):
            return cleaned
        lowered = cleaned.casefold()
        government_site_intent = any(
            term in lowered
            for term in (
                "sitio gubernamental", "sitio oficial", "información pública",
                "informacion publica", "public information", "portal del gobierno",
                "portal gubernamental", "datos abiertos", "transparencia",
            )
        )
        if "chile" in lowered and government_site_intent:
            # Preserve the country's name and official-portal intent on the initial
            # provider call; topic compression would otherwise reduce it to
            # "politics public" and discard the government-site search entirely.
            return (
                "Chile official government public services information "
                "transparency open data ChileAtiende gob.cl"
            )
        topic_tokens = _topic_tokens(cleaned)
        if not 2 <= len(topic_tokens) <= 8:
            return cleaned
        compact = " ".join(topic_tokens)
        lowered = cleaned.casefold()
        if "official" in lowered or "oficial" in lowered:
            compact += " official"
        return compact

    @staticmethod
    def _recovery_query(query: str) -> str:
        """Create one semantically equivalent fallback query after zero usable evidence."""
        cleaned = " ".join(str(query or "").split()).strip()
        topics = _topic_tokens(cleaned)
        compact = " ".join(dict.fromkeys(topics))
        lowered = cleaned.casefold()

        government_site_intent = any(
            term in lowered
            for term in (
                "sitio gubernamental", "sitio oficial", "información pública",
                "informacion publica", "public information", "portal del gobierno",
                "portal gubernamental", "datos abiertos", "transparencia",
            )
        )
        if "chile" in lowered and government_site_intent:
            return (
                "Chile official government public services information "
                "transparency open data ChileAtiende gob.cl"
            )

        # Reframe generic current smartphone/product queries into common web-search
        # vocabulary. Results are still ranked against the untouched original query.
        smartphone_intent = any(term in lowered for term in (
            "smartphone", "smart phone", "mobile phone", "cell phone",
            "teléfono móvil", "telefono movil", "celular",
        ))
        if smartphone_intent:
            return "smartphone specifications current models comparison review"

        if any(term in lowered for term in ("clima", "tiempo", "temperatura", "pronóstico", "pronostico", "weather", "forecast")):
            locality = "Santiago Chile" if "santiago" in lowered else compact
            # English phrases are commonly indexed more consistently by public
            # search pages; rank every candidate against the original user query.
            return f"{locality} weather forecast temperature".strip()
        if any(term in lowered for term in ("econom", "econó", "mercado", "finanz", "financial", "market")):
            locality = "chile" if "chile" in lowered else ""
            return f"{locality} economia economy markets".strip()
        news_intent = any(
            term in lowered
            for term in ("noticia", "noticias", "última hora", "ultima hora", "actualidad", "breaking", "latest news", "news")
        )
        politics_intent = any(
            term in lowered
            for term in ("política", "politica", "presidente", "elecciones", "elección", "eleccion", "gobierno", "congreso", "senado", "ministro")
        )
        if news_intent and politics_intent and "chile" in lowered:
            # The first phrase is too close to the user's wording and tends to
            # return the same small publisher set. Broaden the provider query;
            # every candidate is still ranked against the original user query.
            return (
                "Chile política gobierno Congreso elecciones actualidad noticias "
                "BioBioChile Cooperativa Emol La Tercera 24Horas CNN Chile"
            )
        if news_intent and "chile" in lowered:
            return "Chile últimas noticias hoy"
        if news_intent and compact:
            return f"{compact} latest news today"
        if compact:
            # If a query with several anchors yields no rankable evidence, use a
            # broader provider query in the recovery wave. The final ranking still
            # evaluates every candidate against the original user query.
            if len(topics) >= 3:
                return " ".join(dict.fromkeys(topics[:2]))
            return compact
        return cleaned

    async def _call_provider(self, provider: str, query: str) -> ProviderOutcome:
        state = self.circuits[provider]
        now = time.monotonic()
        if state.is_open(now):
            return ProviderOutcome(
                provider=provider,
                ok=False,
                error_code="CIRCUIT_OPEN",
            )

        started = time.perf_counter()
        try:
            provider_function = self.providers.get(provider) or self.fallback_providers.get(provider)
            if provider_function is None:
                raise RuntimeError("SEARCH_PROVIDER_NOT_REGISTERED")
            results = await asyncio.wait_for(
                provider_function(
                    query,
                    self.provider_total_seconds,
                    self.max_results,
                ),
                timeout=self.provider_total_seconds,
            )
            latency_ms = (time.perf_counter() - started) * 1000.0
            if results:
                state.success()
                return ProviderOutcome(
                    provider=provider,
                    ok=True,
                    results=list(results),
                    latency_ms=round(latency_ms, 2),
                )
            # An empty result set is a valid provider response, not a transport
            # failure. Do not poison the circuit breaker for topical misses.
            state.success()
            return ProviderOutcome(
                provider=provider,
                ok=False,
                error_code="NO_RESULTS",
                latency_ms=round(latency_ms, 2),
            )
        except asyncio.TimeoutError:
            state.failure(time.monotonic())
            return ProviderOutcome(
                provider=provider,
                ok=False,
                error_code="PROVIDER_TIMEOUT",
                latency_ms=round((time.perf_counter() - started) * 1000.0, 2),
            )
        except Exception as exc:
            state.failure(time.monotonic())
            name = type(exc).__name__.upper()
            return ProviderOutcome(
                provider=provider,
                ok=False,
                error_code=f"PROVIDER_ERROR:{name}",
                latency_ms=round((time.perf_counter() - started) * 1000.0, 2),
            )

    async def _bounded_call_provider(self, provider: str, query: str) -> ProviderOutcome:
        """Enforce the provider deadline even if a provider adapter is replaced or misbehaves."""
        started = time.perf_counter()
        timeout = min(
            self.provider_total_seconds + 0.05,
            self.global_budget_seconds,
        )
        try:
            return await asyncio.wait_for(
                self._call_provider(provider, query),
                timeout=max(0.05, timeout),
            )
        except asyncio.TimeoutError:
            self.circuits[provider].failure(time.monotonic())
            return ProviderOutcome(
                provider=provider,
                ok=False,
                error_code="PROVIDER_TIMEOUT",
                latency_ms=round((time.perf_counter() - started) * 1000.0, 2),
            )

    async def _delayed_provider(self, provider: str, query: str) -> ProviderOutcome:
        if self.fallback_delay_seconds:
            await asyncio.sleep(self.fallback_delay_seconds)
        return await self._bounded_call_provider(provider, query)

    async def search(self, query: str, *, fresh: bool = False) -> dict:
        cleaned = " ".join(str(query or "").split()).strip()
        if not cleaned:
            return self._empty_payload(
                query="",
                status="INVALID_QUERY",
                errors=["QUERY_REQUIRED"],
                outcomes=[],
                started=time.perf_counter(),
            )

        cache_key = self._cache_key(cleaned)
        if not fresh:
            cached = self.cache.get(cache_key)
            if cached is not None:
                cached["cache_hit"] = True
                return cached

        started = time.perf_counter()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.global_budget_seconds
        # Reserve almost half of the end-to-end budget for recovery. Without a
        # separate primary deadline, slow providers can consume all 5s and prevent
        # the fallback query from ever running.
        primary_deadline = min(
            deadline,
            loop.time() + max(0.05, self.global_budget_seconds * 0.55),
        )
        provider_query = self._provider_query(cleaned)
        tasks = {
            asyncio.create_task(
                self._delayed_provider(provider, provider_query)
                if provider == "duckduckgo"
                else self._bounded_call_provider(provider, provider_query),
                name=f"search:{provider}",
            )
            for provider in self.providers
        }
        outcomes: list[ProviderOutcome] = []
        pending = set(tasks)
        standard_ready_deadline: float | None = None

        try:
            while pending:
                active_deadline = (
                    min(primary_deadline, standard_ready_deadline)
                    if standard_ready_deadline is not None
                    else primary_deadline
                )
                remaining = active_deadline - loop.time()
                if remaining <= 0:
                    break
                done, pending = await asyncio.wait(
                    pending,
                    timeout=remaining,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if not done:
                    break
                for task in done:
                    try:
                        outcome = task.result()
                    except asyncio.CancelledError:
                        continue
                    except Exception as exc:
                        outcome = ProviderOutcome(
                            provider=task.get_name().split(":")[-1],
                            ok=False,
                            error_code=f"UNEXPECTED:{type(exc).__name__.upper()}",
                        )
                    outcomes.append(outcome)

                merged_now = [
                    {**item, "provider": outcome.provider}
                    for outcome in outcomes
                    if outcome.ok
                    for item in outcome.results
                ]
                ranked_now = rank_results(cleaned, merged_now, realtime=is_realtime_query(cleaned))
                selected_now = deduplicate(ranked_now, self.max_results)
                distinct_domains = {
                    item.get("source_domain") or ""
                    for item in selected_now
                    if item.get("source_domain")
                }
                distinct_providers = {
                    outcome.provider for outcome in outcomes if outcome.ok and outcome.results
                }
                enough_sources = (
                    len(selected_now) >= min(3, self.max_results)
                    and len(distinct_domains) >= min(3, len(selected_now))
                )
                if (
                    enough_sources
                    and len(distinct_providers) >= min(2, len(self.providers))
                ):
                    break
                if (
                    enough_sources
                    and not is_realtime_query(cleaned)
                    and standard_ready_deadline is None
                ):
                    # Standard lookups already have several independent domains.
                    # Give other providers a short corroboration opportunity rather
                    # than serialising user-visible TTFT behind anti-bot/timeouts.
                    standard_ready_deadline = min(
                        primary_deadline,
                        loop.time() + STANDARD_CORROBORATION_WINDOW_SECONDS,
                    )
        finally:
            for task in pending:
                if not task.done():
                    task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

        merged = [
            {**item, "provider": outcome.provider}
            for outcome in outcomes
            if outcome.ok
            for item in outcome.results
        ]
        ranked = rank_results(cleaned, merged, realtime=is_realtime_query(cleaned))
        selected = deduplicate(ranked, self.max_results)

        recovery_query = self._recovery_query(cleaned)
        selected_domains = {
            item.get("source_domain") or ""
            for item in selected
            if item.get("source_domain")
        }
        news_intent = any(
            term in cleaned.casefold()
            for term in ("noticia", "noticias", "última hora", "ultima hora", "actualidad", "breaking", "latest news", "news")
        )
        needs_news_source_diversity = (
            is_realtime_query(cleaned)
            and news_intent
            and len(selected_domains) < min(5, self.max_results)
        )
        successful_providers = {
            outcome.provider for outcome in outcomes if outcome.ok and outcome.results
        }
        needs_provider_diversity = (
            (is_realtime_query(cleaned) or news_intent)
            and len(successful_providers) < min(2, len(self.providers))
        )
        needs_recovery = (
            len(selected) < min(3, self.max_results)
            or len(selected_domains) < min(3, max(1, len(selected)))
            or needs_news_source_diversity
            or needs_provider_diversity
        )
        if (
            needs_recovery
            and recovery_query.casefold() != provider_query.casefold()
            and deadline > loop.time()
        ):
            # Keep every successful recovery result even if another provider hangs.
            # The same end-to-end deadline covers the primary and recovery phases.
            recovery_tasks = {
                asyncio.create_task(
                    self._bounded_call_provider(provider, recovery_query),
                    name=f"search-recovery:{provider}",
                )
                for provider in self.providers
            }
            pending_recovery = set(recovery_tasks)
            try:
                while pending_recovery:
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        break
                    done, pending_recovery = await asyncio.wait(
                        pending_recovery,
                        timeout=remaining,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if not done:
                        break
                    for task in done:
                        try:
                            outcome = task.result()
                        except asyncio.CancelledError:
                            continue
                        except Exception as exc:
                            outcome = ProviderOutcome(
                                provider=task.get_name().split(":")[-1],
                                ok=False,
                                error_code=f"UNEXPECTED:{type(exc).__name__.upper()}",
                            )
                        outcomes.append(outcome)

                    merged_now = [
                        {**item, "provider": outcome.provider}
                        for outcome in outcomes
                        if outcome.ok
                        for item in outcome.results
                    ]
                    ranked_now = rank_results(
                        cleaned, merged_now, realtime=is_realtime_query(cleaned)
                    )
                    selected_now = deduplicate(ranked_now, self.max_results)
                    distinct_domains_now = {
                        item.get("source_domain") or ""
                        for item in selected_now
                        if item.get("source_domain")
                    }
                    distinct_providers_now = {
                        outcome.provider for outcome in outcomes if outcome.ok and outcome.results
                    }
                    if (
                        len(selected_now) >= min(3, self.max_results)
                        and len(distinct_domains_now) >= min(3, len(selected_now))
                        and len(distinct_providers_now) >= min(2, len(self.providers))
                    ):
                        break
            finally:
                for task in pending_recovery:
                    if not task.done():
                        task.cancel()
                if pending_recovery:
                    await asyncio.gather(*pending_recovery, return_exceptions=True)

            merged = [
                {**item, "provider": outcome.provider}
                for outcome in outcomes
                if outcome.ok
                for item in outcome.results
            ]
            ranked = rank_results(cleaned, merged, realtime=is_realtime_query(cleaned))
            selected = deduplicate(ranked, self.max_results)
            if selected:
                provider_query = recovery_query

        if not selected and loop.time() < deadline:
            # Try one independent general-web index only after every normal source
            # returned no rankable evidence. This avoids spending an extra request
            # on searches that are already working.
            remaining = deadline - loop.time()
            try:
                yahoo_outcome = await asyncio.wait_for(
                    self._bounded_call_provider("yahoo", cleaned),
                    timeout=remaining,
                )
            except asyncio.TimeoutError:
                yahoo_outcome = ProviderOutcome(
                    provider="yahoo", ok=False, error_code="PROVIDER_TIMEOUT"
                )
            outcomes.append(yahoo_outcome)
            if yahoo_outcome.ok and yahoo_outcome.results:
                merged = [
                    {**item, "provider": outcome.provider}
                    for outcome in outcomes
                    if outcome.ok
                    for item in outcome.results
                ]
                ranked = rank_results(cleaned, merged, realtime=is_realtime_query(cleaned))
                selected = deduplicate(ranked, self.max_results)
                if selected:
                    provider_query = cleaned

        if not selected and merged:
            # Diagnose relevance rejection without recording query text, titles,
            # snippets, or full URLs. This lets production logs distinguish
            # provider-empty responses from candidates excluded by ranking.
            anchors = set(_topic_tokens(cleaned))
            required_hits = min(2, len(anchors))
            diagnostics = []
            for item in merged[:8]:
                parsed = urlparse(str(item.get("url") or ""))
                url_evidence = (
                    f"{parsed.hostname or ''} "
                    f"{parsed.path.replace('-', ' ').replace('_', ' ').replace('/', ' ')}"
                )
                title_tokens = set(_tokens(str(item.get("title") or "")))
                snippet_tokens = set(_tokens(str(item.get("snippet") or "")))
                url_tokens = set(_tokens(url_evidence))
                combined_tokens = title_tokens | snippet_tokens | url_tokens
                diagnostics.append({
                    "provider": str(item.get("provider") or "unknown"),
                    "title_hits": len(anchors & title_tokens),
                    "snippet_hits": len(anchors & snippet_tokens),
                    "url_hits": len(anchors & url_tokens),
                    "combined_hits": len(anchors & combined_tokens),
                })
            logger.warning(
                "web_search_rank_rejected_candidates anchor_count=%d required_hits=%d "
                "candidate_count=%d candidates=%s",
                len(anchors),
                required_hits,
                len(merged),
                diagnostics,
            )

        errors = [
            f"{outcome.provider}:{outcome.error_code}"
            for outcome in outcomes
            if not outcome.ok and outcome.error_code
        ]
        status = "OK" if selected else (
            "PROVIDER_TIMEOUT"
            if outcomes and all(o.error_code in {"PROVIDER_TIMEOUT", "CIRCUIT_OPEN"} for o in outcomes)
            else "NO_RESULTS"
        )
        payload = self._empty_payload(
            query=cleaned,
            status=status,
            errors=errors,
            outcomes=outcomes,
            started=started,
            results=selected,
            executed_query=provider_query,
        )
        if selected:
            self.cache.put(cache_key, payload, self.cache_ttl_seconds)
        return payload

    def _empty_payload(
        self,
        *,
        query: str,
        status: str,
        errors: list[str],
        outcomes: list[ProviderOutcome],
        started: float,
        results: list[dict] | None = None,
        executed_query: str | None = None,
    ) -> dict:
        selected = list(results or [])
        providers_used = list(dict.fromkeys(o.provider for o in outcomes if o.ok))
        attempted = list(dict.fromkeys(o.provider for o in outcomes))
        winner = next((o.provider for o in outcomes if o.ok and o.results), None)
        realtime = is_realtime_query(query)
        domains = sorted({
            (urlparse(str(item.get("url") or "")).hostname or "").casefold()
            for item in selected
            if str(item.get("url") or "").strip()
        } - {""})
        verification_level = (
            "multi-source"
            if len(domains) >= 2
            else "single-source"
            if selected
            else "none"
        )
        return {
            "ok": bool(selected),
            "status": status,
            "diagnostic_code": None if selected else status,
            "engine": "DEEP33 Search Engine",
            "engine_version": "1.4.0",
            "provider_independent": True,
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "query": query,
            "depth": "realtime" if realtime else "standard",
            "realtime": realtime,
            "queries": [query],
            "queries_executed": [executed_query or query],
            "results": selected,
            "sources": selected,
            "providers": providers_used,
            "providers_attempted": attempted,
            "provider": (
                providers_used[0]
                if len(providers_used) == 1
                else "multi"
                if providers_used
                else None
            ),
            "winner_provider": winner,
            "verification": {
                "level": verification_level,
                "distinct_domains": len(domains),
                "distinct_providers": len(set(providers_used)),
                "corroborated_results": 0,
            },
            "errors": errors[:8],
            "provider_latency_ms": {
                o.provider: o.latency_ms for o in outcomes if o.latency_ms > 0
            },
            "cache_hit": False,
            "latency_ms": round((time.perf_counter() - started) * 1000.0, 2),
        }


_default_orchestrator: WebOrchestrator | None = None


def get_web_orchestrator(max_results: int = 5) -> WebOrchestrator:
    global _default_orchestrator
    if _default_orchestrator is None or _default_orchestrator.max_results != max_results:
        _default_orchestrator = WebOrchestrator(max_results=max_results)
    return _default_orchestrator
