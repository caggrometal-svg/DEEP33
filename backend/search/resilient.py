from __future__ import annotations

import asyncio
import copy
import hashlib
import time
from datetime import datetime, timezone
from urllib.parse import urlparse
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from backend.search.engine import _topic_tokens, deduplicate, is_realtime_query, rank_results
from tools.web_search import _bing_search, _duckduckgo_search, _mojeek_search

PROVIDER_TOTAL_SECONDS = 2.5
GLOBAL_BUDGET_SECONDS = 5.0
FALLBACK_DELAY_SECONDS = 0.35
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
        self.provider_total_seconds = max(0.5, min(2.5, float(provider_total_seconds)))
        self.global_budget_seconds = max(1.0, min(5.0, float(global_budget_seconds)))
        self.fallback_delay_seconds = max(0.0, min(2.0, float(fallback_delay_seconds)))
        self.cache_ttl_seconds = max(30.0, min(120.0, float(cache_ttl_seconds)))
        self.cache = TTLRUCache()
        self.circuits: dict[str, CircuitState] = {
            "bing": CircuitState(),
            "mojeek": CircuitState(),
            "duckduckgo": CircuitState(),
        }
        self.providers: dict[str, Callable[[str, float, int], Awaitable[list[dict]]]] = {
            "bing": self._bing,
            "mojeek": self._mojeek,
            "duckduckgo": self._duckduckgo,
        }

    async def _bing(self, query: str, timeout: float, limit: int) -> list[dict]:
        return await _bing_search(query, timeout, limit)

    async def _mojeek(self, query: str, timeout: float, limit: int) -> list[dict]:
        return await _mojeek_search(query, timeout, limit)

    async def _duckduckgo(self, query: str, timeout: float, limit: int) -> list[dict]:
        return await _duckduckgo_search(query, timeout, limit)

    def _cache_key(self, query: str) -> str:
        normalized = " ".join(query.split()).strip().casefold()
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    @staticmethod
    def _provider_query(query: str) -> str:
        """Reduce provider-side noise without changing the user's ranking query."""
        cleaned = " ".join(str(query or "").split()).strip()
        if not cleaned or is_realtime_query(cleaned):
            return cleaned
        topic_tokens = _topic_tokens(cleaned)
        if not 2 <= len(topic_tokens) <= 8:
            return cleaned
        compact = " ".join(topic_tokens)
        lowered = cleaned.casefold()
        if "official" in lowered or "oficial" in lowered:
            compact += " official"
        return compact

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
            results = await asyncio.wait_for(
                self.providers[provider](
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
            state.failure(time.monotonic())
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

    async def _delayed_provider(self, provider: str, query: str) -> ProviderOutcome:
        if self.fallback_delay_seconds:
            await asyncio.sleep(self.fallback_delay_seconds)
        return await self._call_provider(provider, query)

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
        provider_query = self._provider_query(cleaned)
        tasks = {
            asyncio.create_task(self._call_provider("bing", provider_query), name="search:bing"),
            asyncio.create_task(self._call_provider("mojeek", provider_query), name="search:mojeek"),
            asyncio.create_task(
                self._delayed_provider("duckduckgo", provider_query),
                name="search:duckduckgo",
            ),
        }
        outcomes: list[ProviderOutcome] = []
        pending = set(tasks)

        try:
            deadline = asyncio.get_running_loop().time() + self.global_budget_seconds
            while pending:
                remaining = deadline - asyncio.get_running_loop().time()
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
                if len(selected_now) >= min(3, self.max_results) and len(distinct_domains) >= min(3, len(selected_now)):
                    break
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
        providers_used = [o.provider for o in outcomes if o.ok]
        attempted = [o.provider for o in outcomes]
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
