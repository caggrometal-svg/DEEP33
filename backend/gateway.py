from __future__ import annotations

import json
import logging
import os
import time
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


@dataclass(frozen=True)
class GatewayConfig:
    providers: tuple[GatewayProvider, ...]
    timeout_seconds: float

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
            timeout_seconds=float(os.getenv("AI_TIMEOUT_SECONDS", "45")),
        )

    @property
    def model(self) -> str:
        return self.providers[0].model if self.providers else ""


class AIGateway:
    """Provider-neutral gateway with controlled provider fallback."""

    def __init__(self, config: GatewayConfig | None = None) -> None:
        self.config = config or GatewayConfig.from_env()

    @staticmethod
    def _headers(provider: GatewayProvider) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if provider.api_key:
            headers["Authorization"] = f"Bearer {provider.api_key}"
        return headers

    async def probe(self) -> dict:
        started = time.perf_counter()
        failures: list[str] = []

        for index, provider in enumerate(self.config.providers):
            if not provider.health_url:
                failures.append(f"{provider.name}:health_url_missing")
                continue

            try:
                async with httpx.AsyncClient(timeout=self.config.timeout_seconds) as client:
                    response = await client.get(
                        provider.health_url,
                        headers=self._headers(provider),
                    )

                if 200 <= response.status_code < 300:
                    return {
                        "gateway": "PASS",
                        "provider": provider.name,
                        "model": provider.model or None,
                        "latency_ms": round(
                            (time.perf_counter() - started) * 1000, 2
                        ),
                        "last_success": utc_now(),
                        "last_error": None,
                        "fallback_used": index > 0,
                    }

                failures.append(f"{provider.name}:http:{response.status_code}")
            except httpx.TimeoutException:
                failures.append(f"{provider.name}:timeout")
            except httpx.HTTPError as exc:
                failures.append(f"{provider.name}:{type(exc).__name__}")

        return {
            "gateway": "TIMEOUT" if failures and all("timeout" in item for item in failures) else "FAIL",
            "provider": None,
            "model": self.model or None,
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            "last_success": None,
            "last_error": ";".join(failures) if failures else "no providers configured",
            "fallback_used": False,
        }

    async def complete(self, payload: dict) -> dict:
        saw_timeout = False
        saw_http_error = False
        saw_invalid_response = False

        for provider in self.config.providers:
            try:
                async with httpx.AsyncClient(timeout=self.config.timeout_seconds) as client:
                    response = await client.post(
                        provider.url,
                        json=payload,
                        headers=self._headers(provider),
                    )
                    response.raise_for_status()
            except httpx.TimeoutException:
                saw_timeout = True
                logger.warning("AI provider timeout provider=%s", provider.name)
                continue
            except httpx.HTTPStatusError as exc:
                saw_http_error = True
                logger.warning(
                    "AI provider HTTP failure provider=%s status=%s",
                    provider.name,
                    exc.response.status_code,
                )
                continue
            except httpx.HTTPError as exc:
                saw_http_error = True
                logger.warning(
                    "AI provider transport failure provider=%s error=%s",
                    provider.name,
                    type(exc).__name__,
                )
                continue

            try:
                data = response.json()
            except ValueError:
                saw_invalid_response = True
                logger.warning("AI provider invalid JSON provider=%s", provider.name)
                continue

            if not isinstance(data, dict):
                saw_invalid_response = True
                logger.warning("AI provider invalid payload provider=%s", provider.name)
                continue

            result = dict(data)
            result["_deep33_gateway"] = {
                "provider": provider.name,
                "model": provider.model or data.get("model"),
            }
            return result

        if saw_timeout and not saw_http_error and not saw_invalid_response:
            raise GatewayTimeoutError
        if saw_http_error:
            raise GatewayHTTPError
        raise GatewayInvalidResponseError

    async def stream(self, payload: dict) -> AsyncIterator[bytes]:
        for provider in self.config.providers:
            started_output = False
            try:
                async with httpx.AsyncClient(timeout=self.config.timeout_seconds) as client:
                    async with client.stream(
                        "POST",
                        provider.url,
                        json={**payload, "stream": True},
                        headers=self._headers(provider),
                    ) as response:
                        if response.status_code >= 400:
                            logger.warning(
                                "AI stream provider HTTP failure provider=%s status=%s",
                                provider.name,
                                response.status_code,
                            )
                            continue

                        async for chunk in response.aiter_bytes():
                            if chunk:
                                started_output = True
                                yield chunk
                return
            except httpx.TimeoutException:
                logger.warning("AI stream provider timeout provider=%s", provider.name)
                if started_output:
                    raise GatewayTimeoutError
            except httpx.HTTPError as exc:
                logger.warning(
                    "AI stream provider transport failure provider=%s error=%s",
                    provider.name,
                    type(exc).__name__,
                )
                if started_output:
                    raise GatewayInvalidResponseError

        raise GatewayHTTPError


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
