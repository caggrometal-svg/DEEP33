from __future__ import annotations

import httpx
import pytest

from backend.gateway import AIGateway, GatewayConfig, GatewayHTTPError, GatewayProvider


def provider(name: str, url: str, health_url: str, model: str) -> GatewayProvider:
    return GatewayProvider(name=name, url=url, health_url=health_url, api_key="", model=model)


def test_complete_does_not_fail_over_after_ambiguous_primary_5xx() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.host == "primary.test":
            return httpx.Response(503, request=request, json={"error": "down"})
        return httpx.Response(
            200,
            request=request,
            json={
                "model": "fallback-model",
                "choices": [
                    {"message": {"role": "assistant", "content": "FALLBACK_PASS"}}
                ],
            },
        )

    transport = httpx.MockTransport(handler)
    config = GatewayConfig(
        providers=(
            provider("primary", "https://primary.test/chat", "https://primary.test/models", "primary-model"),
            provider("fallback", "https://fallback.test/chat", "https://fallback.test/models", "fallback-model"),
        ),
        timeout_seconds=2,
        max_retries=0,
    )
    gateway = AIGateway(config)

    original = httpx.AsyncClient

    class Client(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    httpx.AsyncClient = Client
    try:
        import asyncio

        with pytest.raises(GatewayHTTPError):
            asyncio.run(
                gateway.complete(
                    {"model": "requested", "messages": [{"role": "user", "content": "hi"}]}
                )
            )
    finally:
        httpx.AsyncClient = original

    assert calls == ["https://primary.test/chat"]



def test_complete_never_retries_or_fails_over_after_ambiguous_read_timeout() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        raise httpx.ReadTimeout("ambiguous response timeout", request=request)

    transport = httpx.MockTransport(handler)
    config = GatewayConfig(
        providers=(
            provider("primary", "https://primary.test/chat", "https://primary.test/models", "primary-model"),
            provider("fallback", "https://fallback.test/chat", "https://fallback.test/models", "fallback-model"),
        ),
        timeout_seconds=2,
        max_retries=2,
    )
    gateway = AIGateway(config)

    original = httpx.AsyncClient

    class Client(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    httpx.AsyncClient = Client
    try:
        import asyncio

        with pytest.raises(Exception) as exc_info:
            asyncio.run(
                gateway.complete(
                    {"messages": [{"role": "user", "content": "hi"}]},
                    request_id="timeout-test",
                    idempotency_key="timeout-test",
                )
            )
        assert exc_info.type.__name__ == "GatewayTimeoutError"
    finally:
        httpx.AsyncClient = original

    assert calls == ["https://primary.test/chat"]


def test_complete_raises_when_all_providers_fail() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, request=request)

    transport = httpx.MockTransport(handler)
    config = GatewayConfig(
        providers=(provider("primary", "https://primary.test/chat", "https://primary.test/models", "primary-model"),),
        timeout_seconds=2,
    )
    gateway = AIGateway(config)
    original = httpx.AsyncClient

    class Client(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    httpx.AsyncClient = Client
    try:
        import asyncio

        with pytest.raises(GatewayHTTPError):
            asyncio.run(gateway.complete({"messages": [{"role": "user", "content": "hi"}]}))
    finally:
        httpx.AsyncClient = original


def test_complete_fails_over_after_primary_rate_limit() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.host == "primary.test":
            return httpx.Response(429, request=request, json={"error": "rate"})
        return httpx.Response(
            200,
            request=request,
            json={
                "model": "fallback-model",
                "choices": [{"message": {"role": "assistant", "content": "RATE_LIMIT_FALLBACK_PASS"}}],
            },
        )

    transport = httpx.MockTransport(handler)
    config = GatewayConfig(
        providers=(
            provider("primary", "https://primary.test/chat", "https://primary.test/models", "primary-model"),
            provider("fallback", "https://fallback.test/chat", "https://fallback.test/models", "fallback-model"),
        ),
        timeout_seconds=2,
        max_retries=0,
    )
    gateway = AIGateway(config)

    original = httpx.AsyncClient

    class Client(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    httpx.AsyncClient = Client
    try:
        import asyncio
        result = asyncio.run(
            gateway.complete(
                {"messages": [{"role": "user", "content": "hi"}]}
            )
        )
    finally:
        httpx.AsyncClient = original

    assert result["choices"][0]["message"]["content"] == "RATE_LIMIT_FALLBACK_PASS"
    assert calls == ["https://primary.test/chat", "https://fallback.test/chat"]
