from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Awaitable, Callable, Generic, TypeVar

logger = logging.getLogger("deep33.search.resilience")

T = TypeVar("T")


class DiagnosticCode(StrEnum):
    OK = "OK"
    NO_RESULTS = "NO_RESULTS"
    PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"
    PROVIDER_BLOCKED = "PROVIDER_BLOCKED"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    FETCH_TIMEOUT = "FETCH_TIMEOUT"
    FETCH_BLOCKED = "FETCH_BLOCKED"
    FETCH_FAILED = "FETCH_FAILED"
    DNS_FAILED = "DNS_FAILED"
    DEADLINE_REACHED = "DEADLINE_REACHED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


@dataclass(frozen=True)
class NetworkBudgets:
    connect_seconds: float = 1.0
    first_byte_seconds: float = 1.5
    provider_total_seconds: float = 2.5
    fallback_delay_seconds: float = 2.0
    global_search_seconds: float = 5.0


@dataclass
class CircuitBreaker:
    failure_threshold: int = 5
    open_seconds: float = 60.0
    failures: int = 0
    opened_at: float | None = None
    half_open_probe_running: bool = False

    def allow(self, now: float | None = None) -> bool:
        current = time.monotonic() if now is None else now
        if self.opened_at is None:
            return True
        if current - self.opened_at < self.open_seconds:
            return False
        if self.half_open_probe_running:
            return False
        self.half_open_probe_running = True
        return True

    def success(self) -> None:
        self.failures = 0
        self.opened_at = None
        self.half_open_probe_running = False

    def failure(self) -> None:
        self.half_open_probe_running = False
        self.failures += 1
        if self.failures >= self.failure_threshold:
            self.opened_at = time.monotonic()


@dataclass
class ProviderAttempt:
    provider: str
    ok: bool
    elapsed_ms: float
    code: str
    count: int = 0


class TTLRUCache(Generic[T]):
    def __init__(self, *, maxsize: int = 256, ttl_seconds: float = 60.0) -> None:
        self.maxsize = max(1, int(maxsize))
        self.ttl_seconds = max(1.0, float(ttl_seconds))
        self._data: OrderedDict[str, tuple[float, T]] = OrderedDict()
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> T | None:
        now = time.monotonic()
        async with self._lock:
            record = self._data.get(key)
            if record is None:
                return None
            expires_at, value = record
            if expires_at <= now:
                self._data.pop(key, None)
                return None
            self._data.move_to_end(key)
            return value

    async def set(self, key: str, value: T, *, ttl_seconds: float | None = None) -> None:
        ttl = self.ttl_seconds if ttl_seconds is None else max(1.0, float(ttl_seconds))
        async with self._lock:
            self._data[key] = (time.monotonic() + ttl, value)
            self._data.move_to_end(key)
            while len(self._data) > self.maxsize:
                self._data.popitem(last=False)

    async def clear(self) -> None:
        async with self._lock:
            self._data.clear()


class ResilienceController:
    def __init__(
        self,
        *,
        budgets: NetworkBudgets | None = None,
        cache_ttl_seconds: float = 60.0,
        cache_maxsize: int = 256,
    ) -> None:
        self.budgets = budgets or NetworkBudgets()
        self.cache: TTLRUCache[dict[str, Any]] = TTLRUCache(
            maxsize=cache_maxsize,
            ttl_seconds=min(120.0, max(30.0, cache_ttl_seconds)),
        )
        self.breakers: dict[str, CircuitBreaker] = {}
        self._breaker_lock = asyncio.Lock()

    async def breaker(self, provider: str) -> CircuitBreaker:
        async with self._breaker_lock:
            return self.breakers.setdefault(provider, CircuitBreaker())

    async def run_provider(
        self,
        provider: str,
        operation: Callable[[], Awaitable[list[dict[str, Any]]]],
        *,
        timeout_seconds: float | None = None,
    ) -> tuple[list[dict[str, Any]], ProviderAttempt]:
        breaker = await self.breaker(provider)
        if not breaker.allow():
            return [], ProviderAttempt(
                provider=provider,
                ok=False,
                elapsed_ms=0.0,
                code=DiagnosticCode.PROVIDER_BLOCKED,
            )
        started = time.perf_counter()
        timeout = min(
            self.budgets.provider_total_seconds,
            timeout_seconds if timeout_seconds is not None else self.budgets.provider_total_seconds,
        )
        try:
            results = await asyncio.wait_for(operation(), timeout=max(0.1, timeout))
        except asyncio.TimeoutError:
            breaker.failure()
            return [], ProviderAttempt(
                provider=provider,
                ok=False,
                elapsed_ms=(time.perf_counter() - started) * 1000.0,
                code=DiagnosticCode.PROVIDER_TIMEOUT,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            breaker.failure()
            logger.warning(
                "provider_failure provider=%s error=%s",
                provider,
                type(exc).__name__,
            )
            return [], ProviderAttempt(
                provider=provider,
                ok=False,
                elapsed_ms=(time.perf_counter() - started) * 1000.0,
                code=DiagnosticCode.PROVIDER_ERROR,
            )

        if results:
            breaker.success()
            return results, ProviderAttempt(
                provider=provider,
                ok=True,
                elapsed_ms=(time.perf_counter() - started) * 1000.0,
                code=DiagnosticCode.OK,
                count=len(results),
            )

        breaker.failure()
        return [], ProviderAttempt(
            provider=provider,
            ok=False,
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
            code=DiagnosticCode.NO_RESULTS,
        )

    @staticmethod
    async def cancel_and_drain(tasks: list[asyncio.Task[Any]]) -> None:
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    @staticmethod
    def safe_task(
        coro: Awaitable[T],
        *,
        name: str,
        on_error: Callable[[BaseException], None] | None = None,
    ) -> asyncio.Task[T]:
        task: asyncio.Task[T] = asyncio.create_task(coro, name=name)

        def consume(done: asyncio.Task[T]) -> None:
            if done.cancelled():
                return
            try:
                exc = done.exception()
            except asyncio.CancelledError:
                return
            if exc is not None:
                if on_error is not None:
                    on_error(exc)
                else:
                    logger.error(
                        "async_task_failed task=%s error=%s",
                        name,
                        type(exc).__name__,
                    )

        task.add_done_callback(consume)
        return task


def graceful_payload(
    *,
    ok: bool,
    code: DiagnosticCode | str,
    request_id: str,
    results: list[dict[str, Any]] | None = None,
    sources: list[dict[str, Any]] | None = None,
    errors: list[str] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    result_list = list(results or [])
    source_list = list(sources if sources is not None else result_list)
    payload: dict[str, Any] = {
        "ok": bool(ok),
        "code": str(code),
        "request_id": request_id,
        "results": result_list,
        "sources": source_list,
        "errors": list(errors or []),
        "degraded": not ok,
        "timestamp": time.time(),
    }
    payload.update(extra)
    return payload


def structured_observation(event: str, **fields: Any) -> str:
    return json.dumps(
        {"event": event, **fields},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    )


controller = ResilienceController()
