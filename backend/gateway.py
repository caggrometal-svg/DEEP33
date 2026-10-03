from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import AsyncIterator

import httpx

logger = logging.getLogger(__name__)


class GatewayTimeoutError(Exception):
    pass


class GatewayHTTPError(Exception):
    pass


class GatewayInvalidResponseError(Exception):
    pass


@dataclass(frozen=True)
class GatewayProvider:
    name: str
    url: str
    health_url: str
    api_key: str
    model: str


@dataclass
class ProviderCircuit:
    failures: int = 0
    opened_until: float = 0.0
    half_open: bool = False

    def available(self, now: float) -> bool:
        if self.opened_until <= 0:
            return True
        if now >= self.opened_until:
            if not self.half_open:
                self.half_open = True
                return True
            return False
        return False

    def success(self) -> None:
        self.failures = 0
        self.opened_until = 0.0
        self.half_open = False

    def failure(self, threshold: int, cooldown_seconds: float) -> None:
        self.failures += 1
        self.half_open = False
        if self.failures >= threshold:
            self.opened_until = time.monotonic() + cooldown_seconds


@dataclass(frozen=True)
class GatewayConfig:
    providers: tuple[GatewayProvider, ...]
    timeout_seconds: float
    provider_timeout_seconds: float = 18.0
    max_retries: int = 1
    backoff_seconds: float = 0.6
    circuit_failure_threshold: int = 3
    circuit_cooldown_seconds: float = 30.0

    @classmethod
    def from_env(cls) -> "GatewayConfig":
        primary = GatewayProvider(
            name=os.getenv("AI_GATEWAY_PROVIDER", "kilo").strip() or "kilo",
            url=os.getenv(
                "AI_GATEWAY_URL",
                "https://api.kilo.ai/api/gateway/chat/completions",
            ).strip(),
            health_url=os.getenv(
                "AI_GATEWAY_HEALTH_URL",
                "https://api.kilo.ai/api/gateway/models",
            ).strip(),
            api_key=os.getenv("AI_GATEWAY_API_KEY", "").strip(),
            model=os.getenv("AI_GATEWAY_MODEL", "kilo-auto/small").strip(),
        )

        providers = [primary]
        raw_fallbacks = os.getenv("AI_GATEWAY_FALLBACKS_JSON", "").strip()
        if raw_fallbacks:
            try:
                fallback_values = json.loads(raw_fallbacks)
            except json.JSONDecodeError as exc:
                raise ValueError("AI_GATEWAY_FALLBACKS_JSON must be valid JSON") from exc
            if not isinstance(fallback_values, list):
                raise ValueError("AI_GATEWAY_FALLBACKS_JSON must be a JSON array")
            for index, item in enumerate(fallback_values, start=1):
                if not isinstance(item, dict):
                    raise ValueError(
                        f"AI_GATEWAY_FALLBACKS_JSON item {index} must be an object"
                    )
                providers.append(
                    GatewayProvider(
                        name=str(item.get("name", f"fallback-{index}")).strip(),
                        url=str(item.get("url", "")).strip(),
                        health_url=str(item.get("health_url", "")).strip(),
                        api_key=str(item.get("api_key", "")).strip(),
                        model=str(item.get("model", "")).strip(),
                    )
                )

        return cls(
            providers=tuple(provider for provider in providers if provider.url),
            timeout_seconds=max(5.0, float(os.getenv("AI_TIMEOUT_SECONDS", "75"))),
            provider_timeout_seconds=max(
                3.0, float(os.getenv("AI_PROVIDER_TIMEOUT_SECONDS", "18"))
            ),
            max_retries=max(0, min(2, int(os.getenv("AI_PROVIDER_MAX_RETRIES", "1")))),
            backoff_seconds=max(
                0.05, float(os.getenv("AI_RETRY_BACKOFF_SECONDS", "0.6"))
            ),
            circuit_failure_threshold=max(
                1, int(os.getenv("AI_CIRCUIT_FAILURE_THRESHOLD", "3"))
            ),
            circuit_cooldown_seconds=max(
                5.0, float(os.getenv("AI_CIRCUIT_COOLDOWN_SECONDS", "30"))
            ),
        )

    @property
    def model(self) -> str:
        return self.providers[0].model if self.providers else ""


class AIGateway:
    """Provider-neutral gateway with deterministic fallback, retry and circuit breaker."""

    def __init__(self, config: GatewayConfig | None = None) -> None:
        self.config = config or GatewayConfig.from_env()
        self._circuits = {provider.name: ProviderCircuit() for provider in self.config.providers}
        self._http_client: httpx.AsyncClient | None = None
        self._http_loop: asyncio.AbstractEventLoop | None = None
        self._latency_samples: dict[str, deque[float]] = {
            provider.name: deque(maxlen=20) for provider in self.config.providers
        }
        self._http_loop: asyncio.AbstractEventLoop | None = None

    def _record_latency(self, provider: GatewayProvider, elapsed_ms: float) -> None:
        self._latency_samples.setdefault(provider.name, deque(maxlen=20)).append(elapsed_ms)

    def _ordered_providers(self) -> list[GatewayProvider]:
        providers = list(self.config.providers)
        if any(len(self._latency_samples.get(provider.name, ())) < 3 for provider in providers):
            return providers
        return sorted(
            providers,
            key=lambda provider: (
                sum(self._latency_samples.get(provider.name, ()))
                / max(1, len(self._latency_samples.get(provider.name, ()))),
                self.config.providers.index(provider),
            ),
        )

    def latency_snapshot(self) -> dict[str, dict[str, float | int | None]]:
        output = {}
        for provider in self.config.providers:
            samples = list(self._latency_samples.get(provider.name, ()))
            if not samples:
                output[provider.name] = {"samples": 0, "p50_ms": None, "p95_ms": None}
                continue
            ordered = sorted(samples)
            p50 = ordered[min(len(ordered) - 1, int(round(0.50 * (len(ordered) - 1))))]
            p95 = ordered[min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))]
            output[provider.name] = {
                "samples": len(ordered),
                "p50_ms": round(p50, 2),
                "p95_ms": round(p95, 2),
            }
        return output

    def _client(self) -> httpx.AsyncClient:
        loop = asyncio.get_running_loop()
        if self._http_client is None or self._http_loop is not loop:
            self._http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.config.timeout_seconds),
                follow_redirects=True,
                limits=httpx.Limits(
                    max_connections=20,
                    max_keepalive_connections=10,
                    keepalive_expiry=30.0,
                ),
            )
            self._http_loop = loop
        return self._http_client

    async def close(self) -> None:
        if self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None
            self._http_loop = None

    @staticmethod
    def _headers(
        provider: GatewayProvider,
        request_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if provider.api_key:
            headers["Authorization"] = f"Bearer {provider.api_key}"
        if request_id:
            headers["X-Request-ID"] = request_id
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return headers

    def _circuit(self, provider: GatewayProvider) -> ProviderCircuit:
        return self._circuits.setdefault(provider.name, ProviderCircuit())

    def _sleep_budget(self, attempt: int, deadline: float | None) -> float:
        delay = self.config.backoff_seconds * (2**attempt) + random.uniform(0, 0.25)
        if deadline is None:
            return delay
        remaining = max(0.0, deadline - time.monotonic())
        return min(delay, remaining)

    @staticmethod
    def _remaining(deadline: float | None, default: float) -> float:
        if deadline is None:
            return default
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise GatewayTimeoutError
        return max(0.5, min(default, remaining))

    @staticmethod
    def _provider_payload(payload: dict, provider: GatewayProvider) -> dict:
        result = dict(payload)
        if provider.model:
            result["model"] = provider.model
        elif not result.get("model"):
            result.pop("model", None)
        return result

    @staticmethod
    def _classify_http_status(status: int) -> str:
        if status in {401, 403}:
            return "auth"
        if status == 429:
            return "rate-limit"
        if 500 <= status <= 599:
            return "http"
        return "invalid-response"

    @staticmethod
    def _is_retryable_status(status: int) -> bool:
        # 5xx is ambiguous for inference requests: the provider may have accepted the
        # request before returning the error. Retrying/failing over can duplicate inference.
        return status == 429

    async def probe(self) -> dict:
        started = time.perf_counter()
        failures: list[str] = []

        for index, provider in enumerate(self.config.providers):
            try:
                timeout = self.config.provider_timeout_seconds
                response = await self._client().get(
                    provider.health_url,
                    headers=self._headers(provider),
                    timeout=timeout,
                )

                if 200 <= response.status_code < 300:
                    return {
                        "gateway": "PASS",
                        "provider": provider.name,
                        "model": provider.model or None,
                        "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                        "last_success": utc_now(),
                        "last_error": None,
                        "fallback_used": index > 0,
                        "circuit_open": False,
                    }

                failures.append(f"{provider.name}:http:{response.status_code}")
            except httpx.TimeoutException:
                failures.append(f"{provider.name}:timeout")
            except httpx.HTTPError as exc:
                failures.append(f"{provider.name}:{type(exc).__name__}")

        return {
            "gateway": "TIMEOUT" if failures and all(":timeout" in item for item in failures) else "FAIL",
            "provider": None,
            "model": self.model or None,
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            "last_success": None,
            "last_error": ";".join(failures) if failures else "no providers configured",
            "fallback_used": False,
            "circuit_open": False,
        }

    async def complete(
        self,
        payload: dict,
        *,
        request_id: str | None = None,
        idempotency_key: str | None = None,
        deadline: float | None = None,
    ) -> dict:
        saw_timeout = False
        saw_http_error = False
        saw_invalid_response = False

        for provider in self._ordered_providers():
            circuit = self._circuit(provider)
            now = time.monotonic()
            if not circuit.available(now):
                logger.warning(
                    "provider_skipped_circuit_open provider=%s",
                    provider.name,
                )
                continue

            provider_succeeded = False
            provider_failed_transiently = False

            for attempt in range(self.config.max_retries + 1):
                try:
                    timeout = self._remaining(
                        deadline, self.config.provider_timeout_seconds
                    )
                    body = self._provider_payload(payload, provider)
                    response = await self._client().post(
                        provider.url,
                        json=body,
                        headers=self._headers(provider, request_id, idempotency_key),
                        timeout=timeout,
                    )

                    if response.status_code >= 400:
                        status = response.status_code
                        error_class = self._classify_http_status(status)
                        saw_http_error = True
                        provider_failed_transiently = self._is_retryable_status(status)
                        logger.warning(
                            "ai_provider_http_failure request_id=%s provider=%s status=%s error_class=%s attempt=%s",
                            request_id,
                            provider.name,
                            status,
                            error_class,
                            attempt + 1,
                        )
                        if 500 <= status <= 599:
                            # A provider 5xx is ambiguous after a POST. Do not retry or
                            # switch providers because the inference may already exist.
                            circuit.failure(
                                self.config.circuit_failure_threshold,
                                self.config.circuit_cooldown_seconds,
                            )
                            raise GatewayHTTPError
                        if provider_failed_transiently and attempt < self.config.max_retries:
                            delay = self._sleep_budget(attempt, deadline)
                            if delay > 0:
                                await asyncio.sleep(delay)
                            continue
                        break

                    try:
                        data = response.json()
                    except ValueError:
                        saw_invalid_response = True
                        logger.warning(
                            "ai_provider_invalid_json request_id=%s provider=%s",
                            request_id,
                            provider.name,
                        )
                        break

                    if not isinstance(data, dict):
                        saw_invalid_response = True
                        logger.warning(
                            "ai_provider_invalid_payload request_id=%s provider=%s",
                            request_id,
                            provider.name,
                        )
                        break

                    elapsed_ms = (time.perf_counter() - started) * 1000
                    self._record_latency(provider, elapsed_ms)
                    result = dict(data)
                    result["_deep33_gateway"] = {
                        "provider": provider.name,
                        "model": provider.model or data.get("model"),
                    }
                    circuit.success()
                    provider_succeeded = True
                    return result

                except httpx.ConnectTimeout:
                    saw_timeout = True
                    provider_failed_transiently = True
                    logger.warning(
                        "ai_provider_connect_timeout request_id=%s provider=%s attempt=%s",
                        request_id,
                        provider.name,
                        attempt + 1,
                    )
                    if attempt < self.config.max_retries:
                        delay = self._sleep_budget(attempt, deadline)
                        if delay > 0:
                            await asyncio.sleep(delay)
                        continue
                    break
                except (httpx.ReadTimeout, httpx.WriteTimeout):
                    saw_timeout = True
                    provider_failed_transiently = False
                    logger.warning(
                        "ai_provider_ambiguous_timeout request_id=%s provider=%s attempt=%s",
                        request_id,
                        provider.name,
                        attempt + 1,
                    )
                    raise GatewayTimeoutError
                except httpx.TimeoutException:
                    saw_timeout = True
                    provider_failed_transiently = True
                    logger.warning(
                        "ai_provider_timeout request_id=%s provider=%s attempt=%s",
                        request_id,
                        provider.name,
                        attempt + 1,
                    )
                    if attempt < self.config.max_retries:
                        delay = self._sleep_budget(attempt, deadline)
                        if delay > 0:
                            await asyncio.sleep(delay)
                        continue
                    break
                except httpx.HTTPError as exc:
                    saw_http_error = True
                    provider_failed_transiently = False
                    logger.warning(
                        "ai_provider_transport_failure request_id=%s provider=%s error=%s",
                        request_id,
                        provider.name,
                        type(exc).__name__,
                    )
                    break

            if not provider_succeeded and provider_failed_transiently:
                circuit.failure(
                    self.config.circuit_failure_threshold,
                    self.config.circuit_cooldown_seconds,
                )

        if saw_timeout and not saw_http_error and not saw_invalid_response:
            raise GatewayTimeoutError
        if saw_http_error:
            raise GatewayHTTPError
        raise GatewayInvalidResponseError

    async def diagnostic_inference(
        self,
        *,
        request_id: str | None = None,
        deadline: float | None = None,
    ) -> dict:
        return await self.complete(
            {
                "messages": [
                    {
                        "role": "system",
                        "content": "Return the requested diagnostic token exactly.",
                    },
                    {"role": "user", "content": "DEEP33_DIAGNOSTIC_OK"},
                ],
            },
            request_id=request_id,
            idempotency_key=f"diagnostic-{request_id or uuid4_short()}",
            deadline=deadline,
        )

    async def stream(
        self,
        payload: dict,
        *,
        request_id: str | None = None,
        idempotency_key: str | None = None,
        deadline: float | None = None,
    ) -> AsyncIterator[bytes]:
        saw_timeout = False
        saw_http_error = False
        saw_invalid_response = False

        for provider in self.config.providers:
            circuit = self._circuit(provider)
            if not circuit.available(time.monotonic()):
                continue

            provider_succeeded = False
            transient_failure = False

            for attempt in range(self.config.max_retries + 1):
                started_output = False
                try:
                    timeout = self._remaining(
                        deadline, self.config.provider_timeout_seconds
                    )
                    body = {**self._provider_payload(payload, provider), "stream": True}
                    async with self._client().stream(
                        "POST",
                        provider.url,
                        json=body,
                        headers=self._headers(provider, request_id, idempotency_key),
                        timeout=timeout,
                    ) as response:
                            if response.status_code >= 400:
                                status = response.status_code
                                error_class = self._classify_http_status(status)
                                saw_http_error = True
                                transient_failure = self._is_retryable_status(status)
                                logger.warning(
                                    "ai_stream_provider_http_failure request_id=%s provider=%s status=%s error_class=%s attempt=%s",
                                    request_id,
                                    provider.name,
                                    status,
                                    error_class,
                                    attempt + 1,
                                )
                                if 500 <= status <= 599:
                                    raise GatewayHTTPError
                            else:
                                request_completed_at = time.perf_counter()
                                async for chunk in response.aiter_bytes():
                                    if chunk:
                                        started_output = True
                                        yield chunk
                                provider_succeeded = True
                                self._record_latency(
                                    provider,
                                    (time.perf_counter() - started_output if started_output else time.perf_counter() - request_completed_at) * 1000,
                                )
                                circuit.success()
                                return

                    if provider_succeeded:
                        return
                    if transient_failure and attempt < self.config.max_retries and not started_output:
                        delay = self._sleep_budget(attempt, deadline)
                        if delay > 0:
                            await asyncio.sleep(delay)
                        continue
                    break
                except httpx.TimeoutException:
                    saw_timeout = True
                    transient_failure = True
                    logger.warning(
                        "ai_stream_provider_timeout request_id=%s provider=%s attempt=%s",
                        request_id,
                        provider.name,
                        attempt + 1,
                    )
                    if started_output:
                        raise GatewayTimeoutError
                    if attempt < self.config.max_retries:
                        delay = self._sleep_budget(attempt, deadline)
                        if delay > 0:
                            await asyncio.sleep(delay)
                        continue
                    break
                except httpx.HTTPError as exc:
                    saw_http_error = True
                    logger.warning(
                        "ai_stream_provider_transport_failure request_id=%s provider=%s error=%s",
                        request_id,
                        provider.name,
                        type(exc).__name__,
                    )
                    break

            if transient_failure:
                circuit.failure(
                    self.config.circuit_failure_threshold,
                    self.config.circuit_cooldown_seconds,
                )

        if saw_timeout and not saw_http_error and not saw_invalid_response:
            raise GatewayTimeoutError
        if saw_http_error:
            raise GatewayHTTPError
        raise GatewayInvalidResponseError


def uuid4_short() -> str:
    return f"{time.time_ns():x}"[-16:]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
