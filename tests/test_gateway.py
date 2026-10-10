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



@pytest.mark.parametrize("timeout_type", [httpx.ReadTimeout, httpx.WriteTimeout])
def test_complete_fails_over_once_after_ambiguous_timeout_without_retrying_primary(timeout_type) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.host == "primary.test":
            raise timeout_type("ambiguous provider timeout", request=request)
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

        result = asyncio.run(
            gateway.complete(
                {"messages": [{"role": "user", "content": "hi"}]},
                request_id="timeout-failover-test",
                idempotency_key="timeout-failover-test",
            )
        )
    finally:
        httpx.AsyncClient = original

    assert result["choices"][0]["message"]["content"] == "FALLBACK_PASS"
    assert result["_deep33_gateway"]["provider"] == "fallback"
    assert result["_deep33_gateway"]["fallback_used"] is True
    assert calls == [
        "https://primary.test/chat",
        "https://fallback.test/chat",
    ]

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


def test_complete_opens_circuit_on_first_primary_404_and_uses_fallback() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.host == "primary.test":
            return httpx.Response(404, request=request, json={"error": "model or route not found"})
        return httpx.Response(
            200,
            request=request,
            json={
                "model": "fallback-model",
                "choices": [{"message": {"role": "assistant", "content": "FALLBACK_NON_EMPTY"}}],
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
        circuit_failure_threshold=3,
        circuit_cooldown_seconds=30,
    )
    gateway = AIGateway(config)
    original = httpx.AsyncClient

    class Client(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    async def exercise() -> list[dict]:
        return [
            await gateway.complete({"messages": [{"role": "user", "content": "hi"}]})
            for _ in range(4)
        ]

    httpx.AsyncClient = Client
    try:
        import asyncio
        outputs = asyncio.run(exercise())
    finally:
        httpx.AsyncClient = original

    assert all(output["choices"][0]["message"]["content"] == "FALLBACK_NON_EMPTY" for output in outputs)
    assert calls.count("https://primary.test/chat") == 1
    assert calls.count("https://fallback.test/chat") == 4
    assert gateway._circuit(config.providers[0]).opened_until > 0


def test_stream_provider_order_prefers_ttft_after_learning():
    primary = provider("primary", "https://primary.test/chat", "https://primary.test/models", "primary-model")
    fallback = provider("fallback", "https://fallback.test/chat", "https://fallback.test/models", "fallback-model")
    gateway_instance = AIGateway(
        GatewayConfig(
            providers=(primary, fallback),
            timeout_seconds=2,
        )
    )

    for value in (100.0, 110.0, 90.0):
        gateway_instance._record_latency(primary, value)
    for value in (200.0, 210.0, 190.0):
        gateway_instance._record_latency(fallback, value)

    for value in (120.0, 130.0, 110.0):
        gateway_instance._record_ttft(primary, value)
    for value in (20.0, 25.0, 30.0):
        gateway_instance._record_ttft(fallback, value)

    assert gateway_instance._ordered_providers(prefer_ttft=True)[0].name == "fallback"


def test_stream_fails_over_on_provider_policy_block_text_before_emitting_it() -> None:
    calls: list[str] = []

    class PolicyBlockStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"choices":[{"delta":{"content":"Automated bulk"}}]}\n\n'
            yield b'data: {"choices":[{"delta":{"content":" tasks detected. Future requests will be blocked."}}]}\n\n'
            yield b"data: [DONE]\n\n"

        async def aclose(self) -> None:
            return None

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.host == "primary.test":
            return httpx.Response(
                200,
                request=request,
                headers={"content-type": "text/event-stream"},
                stream=PolicyBlockStream(),
            )
        return httpx.Response(
            200,
            request=request,
            headers={"content-type": "text/event-stream"},
            content=(
                b'data: {"choices":[{"delta":{"content":"FALLBACK_STREAM_NON_EMPTY"}}]}\n\n'
                b"data: [DONE]\n\n"
            ),
        )

    transport = httpx.MockTransport(handler)
    config = GatewayConfig(
        providers=(
            provider("primary", "https://primary.test/chat", "https://primary.test/models", "primary-model"),
            provider("fallback", "https://fallback.test/chat", "https://fallback.test/models", "fallback-model"),
        ),
        timeout_seconds=2,
        max_retries=0,
        circuit_failure_threshold=3,
        circuit_cooldown_seconds=30,
    )
    gateway = AIGateway(config)
    original = httpx.AsyncClient

    class Client(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    async def one_stream() -> bytes:
        output = bytearray()
        async for part in gateway.stream({"messages": [{"role": "user", "content": "hi"}]}):
            output.extend(part)
        return bytes(output)

    httpx.AsyncClient = Client
    try:
        import asyncio
        output = asyncio.run(one_stream())
    finally:
        httpx.AsyncClient = original

    assert calls == ["https://primary.test/chat", "https://fallback.test/chat"]
    assert b"Automated bulk tasks detected" not in output
    assert output.count(b"FALLBACK_STREAM_NON_EMPTY") == 1
    assert output.count(b"data: [DONE]") == 1


def test_stream_opens_circuit_on_first_primary_404_and_uses_fallback() -> None:
    calls: list[str] = []

    class UnreadErrorBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'{"error":"model route not found"}'

        async def aclose(self) -> None:
            return None

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.host == "primary.test":
            # An error response returned by a real streaming transport has not
            # been read yet when the gateway logs its bounded error hint.
            return httpx.Response(
                404,
                request=request,
                headers={"content-type": "application/json"},
                stream=UnreadErrorBody(),
            )
        return httpx.Response(
            200,
            request=request,
            headers={"content-type": "text/event-stream"},
            content=(
                b'data: {"choices":[{"delta":{"content":"FALLBACK_STREAM_NON_EMPTY"}}]}\n\n'
                b'data: [DONE]\n\n'
            ),
        )

    transport = httpx.MockTransport(handler)
    config = GatewayConfig(
        providers=(
            provider("primary", "https://primary.test/chat", "https://primary.test/models", "primary-model"),
            provider("fallback", "https://fallback.test/chat", "https://fallback.test/models", "fallback-model"),
        ),
        timeout_seconds=2,
        max_retries=0,
        circuit_failure_threshold=3,
        circuit_cooldown_seconds=30,
    )
    gateway = AIGateway(config)
    original = httpx.AsyncClient

    class Client(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    async def one_stream() -> bytes:
        parts = []
        async for part in gateway.stream({"messages": [{"role": "user", "content": "hi"}]}):
            parts.append(part)
        return b"".join(parts)

    async def exercise() -> list[bytes]:
        return [await one_stream() for _ in range(4)]

    httpx.AsyncClient = Client
    try:
        import asyncio
        outputs = asyncio.run(exercise())
    finally:
        httpx.AsyncClient = original

    assert all(b"FALLBACK_STREAM_NON_EMPTY" in output for output in outputs)
    assert all(output.count(b"FALLBACK_STREAM_NON_EMPTY") == 1 for output in outputs)
    assert calls.count("https://primary.test/chat") == 1
    assert calls.count("https://fallback.test/chat") == 4
    assert gateway._circuit(config.providers[0]).opened_until > 0

def test_provider_error_hint_extracts_message_and_caps_length() -> None:
    response = httpx.Response(
        404,
        json={"error": {"message": "model route not found"}},
    )
    assert AIGateway._provider_error_hint(response) == "model route not found"

    oversized = httpx.Response(
        404,
        json={"error": {"message": "x" * 500}},
    )
    assert len(AIGateway._provider_error_hint(oversized)) == 240


def test_default_model_is_explicitly_free(monkeypatch):
    monkeypatch.delenv("AI_GATEWAY_MODEL", raising=False)
    monkeypatch.delenv("AI_GATEWAY_FALLBACKS_JSON", raising=False)
    monkeypatch.delenv("AI_GATEWAY_API_KEY", raising=False)

    config = GatewayConfig.from_env()

    assert config.model == "dots-studio/dots-3-note-preview:free"


def test_stream_fails_over_after_empty_200_without_forwarding_empty_chunks() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.host == "primary.test":
            return httpx.Response(
                200,
                request=request,
                headers={"content-type": "text/event-stream"},
                content=b"data: [DONE]\n\n",
            )
        return httpx.Response(
            200,
            request=request,
            headers={"content-type": "text/event-stream"},
            content=(
                b'data: {"choices":[{"delta":{"content":"FALLBACK_STREAM_NON_EMPTY"}}]}\n\n'
                b"data: [DONE]\n\n"
            ),
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

    async def one_stream() -> bytes:
        output = bytearray()
        async for part in gateway.stream({"messages": [{"role": "user", "content": "hi"}]}):
            output.extend(part)
        return bytes(output)

    httpx.AsyncClient = Client
    try:
        import asyncio
        output = asyncio.run(one_stream())
    finally:
        httpx.AsyncClient = original

    assert calls == ["https://primary.test/chat", "https://fallback.test/chat"]
    assert output.count(b"FALLBACK_STREAM_NON_EMPTY") == 1
    assert output.count(b"data: [DONE]") == 1
