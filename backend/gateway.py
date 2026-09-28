from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import AsyncIterator

import httpx


class GatewayTimeoutError(Exception):
    pass


class GatewayHTTPError(Exception):
    pass


class GatewayInvalidResponseError(Exception):
    pass


@dataclass(frozen=True)
class GatewayConfig:
    url: str
    health_url: str
    api_key: str
    model: str
    timeout_seconds: float

    @classmethod
    def from_env(cls) -> "GatewayConfig":
        return cls(
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
            timeout_seconds=float(os.getenv("AI_TIMEOUT_SECONDS", "45")),
        )


class AIGateway:
    """Provider-neutral OpenAI-compatible gateway adapter for DEEP33."""

    def __init__(self, config: GatewayConfig | None = None) -> None:
        self.config = config or GatewayConfig.from_env()

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        return headers

    async def probe(self) -> dict:
        if not self.config.health_url:
            return {
                "gateway": "NOT_VERIFIED",
                "provider": None,
                "model": self.config.model or None,
                "latency_ms": None,
                "last_success": None,
                "last_error": "AI_GATEWAY_HEALTH_URL is not configured",
            }

        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=self.config.timeout_seconds) as client:
                response = await client.get(
                    self.config.health_url,
                    headers=self._headers(),
                )
            latency_ms = round((time.perf_counter() - started) * 1000, 2)
            passed = 200 <= response.status_code < 300
            return {
                "gateway": "PASS" if passed else "FAIL",
                "provider": response.headers.get("x-provider"),
                "model": self.config.model or None,
                "latency_ms": latency_ms,
                "last_success": utc_now() if passed else None,
                "last_error": None if passed else f"http:{response.status_code}",
            }
        except httpx.TimeoutException:
            return {
                "gateway": "TIMEOUT",
                "provider": None,
                "model": self.config.model or None,
                "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                "last_success": None,
                "last_error": "timeout",
            }
        except httpx.HTTPError as exc:
            return {
                "gateway": "FAIL",
                "provider": None,
                "model": self.config.model or None,
                "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                "last_success": None,
                "last_error": type(exc).__name__,
            }

    async def complete(self, payload: dict) -> dict:
        try:
            async with httpx.AsyncClient(timeout=self.config.timeout_seconds) as client:
                response = await client.post(
                    self.config.url,
                    json=payload,
                    headers=self._headers(),
                )
                response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise GatewayTimeoutError from exc
        except httpx.HTTPStatusError as exc:
            raise GatewayHTTPError from exc
        except httpx.HTTPError as exc:
            raise GatewayInvalidResponseError from exc

        try:
            data = response.json()
        except ValueError as exc:
            raise GatewayInvalidResponseError from exc

        if not isinstance(data, dict):
            raise GatewayInvalidResponseError

        return data

    async def stream(self, payload: dict) -> AsyncIterator[bytes]:
        try:
            async with httpx.AsyncClient(timeout=self.config.timeout_seconds) as client:
                async with client.stream(
                    "POST",
                    self.config.url,
                    json={**payload, "stream": True},
                    headers=self._headers(),
                ) as response:
                    if response.status_code >= 400:
                        raise GatewayHTTPError
                    async for chunk in response.aiter_bytes():
                        if chunk:
                            yield chunk
        except httpx.TimeoutException as exc:
            raise GatewayTimeoutError from exc
        except httpx.HTTPError as exc:
            raise GatewayInvalidResponseError from exc


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
