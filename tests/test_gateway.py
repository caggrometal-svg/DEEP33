from __future__ import annotations

import asyncio
import time
import httpx
import pytest

from backend.gateway import AIGateway, GatewayConfig, GatewayHTTPError, GatewayProvider


def provider(name: str, url: str, health_url: str, model: str) -> GatewayProvider:
    return GatewayProvider(name=name, url=url, health_url=health_url, api_key="", model=model)


def test_complete_fails_over_once_after_primary_5xx_without_retrying_primary() -> None:
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
        result = asyncio.run(
            gateway.complete(
                {"model": "requested", "messages": [{"role": "user", "content": "hi"}]},
                request_id="5xx-failover-test",
                idempotency_key="5xx-failover-test",
            )
        )
    finally:
        httpx.AsyncClient = original

    assert result["choices"][0]["message"]["content"] == "FALLBACK_PASS"
    assert result["_deep33_gateway"]["provider"] == "fallback"
    assert calls == [
        "https://primary.test/chat",
        "https://fallback.test/chat",
    ]


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

    assert config.model == "kilo-auto/free"


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




def test_stream_fails_over_when_provider_emits_metadata_but_no_visible_text_before_budget() -> None:
    calls: list[str] = []

    class SlowMetadataStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n'
            await asyncio.sleep(0.25)
            yield b'data: {"choices":[{"delta":{"content":"TOO_LATE"}}]}\n\n'
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
                stream=SlowMetadataStream(),
            )
        return httpx.Response(
            200,
            request=request,
            headers={"content-type": "text/event-stream"},
            content=(
                b'data: {"choices":[{"delta":{"content":"FAST_FALLBACK"}}]}\n\n'
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
        provider_timeout_seconds=2,
        first_visible_timeout_seconds=0.08,
        max_retries=0,
    )
    gateway = AIGateway(config)
    original = httpx.AsyncClient

    class Client(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    async def run_stream() -> bytes:
        output = bytearray()
        async for part in gateway.stream({"messages": [{"role": "user", "content": "hi"}]}):
            output.extend(part)
        return bytes(output)

    httpx.AsyncClient = Client
    try:
        started = __import__("time").perf_counter()
        output = asyncio.run(run_stream())
        elapsed = __import__("time").perf_counter() - started
    finally:
        httpx.AsyncClient = original

    assert calls == ["https://primary.test/chat", "https://fallback.test/chat"]
    assert b"FAST_FALLBACK" in output
    assert b"TOO_LATE" not in output
    assert elapsed < 0.2


def test_stream_ttft_uses_first_visible_text_not_role_metadata() -> None:
    calls: list[str] = []

    class MetadataThenTextStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n'
            await asyncio.sleep(0.04)
            yield b'data: {"choices":[{"delta":{"content":"VISIBLE_FIRST_TOKEN"}}]}\n\n'
            yield b"data: [DONE]\n\n"

        async def aclose(self) -> None:
            return None

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(
            200,
            request=request,
            headers={"content-type": "text/event-stream"},
            stream=MetadataThenTextStream(),
        )

    transport = httpx.MockTransport(handler)
    config = GatewayConfig(
        providers=(
            provider("primary", "https://primary.test/chat", "https://primary.test/models", "primary-model"),
        ),
        timeout_seconds=2,
        provider_timeout_seconds=2,
        max_retries=0,
    )
    gateway = AIGateway(config)
    original = httpx.AsyncClient

    class Client(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    async def run_stream() -> bytes:
        output = bytearray()
        async for part in gateway.stream({"messages": [{"role": "user", "content": "hi"}]}):
            output.extend(part)
        return bytes(output)

    httpx.AsyncClient = Client
    try:
        output = asyncio.run(run_stream())
    finally:
        httpx.AsyncClient = original

    samples = list(gateway._ttft_samples["primary"])
    assert calls == ["https://primary.test/chat"]
    assert b"VISIBLE_FIRST_TOKEN" in output
    assert len(samples) == 1
    assert samples[0] >= 30.0


def test_gateway_config_excludes_gemma4_fallback(monkeypatch) -> None:
    import json

    monkeypatch.setenv("AI_GATEWAY_PROVIDER", "kilo")
    monkeypatch.setenv("AI_GATEWAY_URL", "https://api.kilo.ai/api/gateway/chat/completions")
    monkeypatch.setenv("AI_GATEWAY_HEALTH_URL", "https://api.kilo.ai/api/gateway/models")
    monkeypatch.setenv("AI_GATEWAY_MODEL", "dots-studio/dots-3-note-preview:free")
    monkeypatch.setenv(
        "AI_GATEWAY_FALLBACKS_JSON",
        json.dumps([
            {
                "name": "forbidden-gemma",
                "url": "https://fallback.example.com/chat",
                "health_url": "https://fallback.example.com/models",
                "api_key": "",
                "model": "google/gemma-4-26b-a4b-it",
            },
            {
                "name": "allowed-fallback",
                "url": "https://allowed.example.com/chat",
                "health_url": "https://allowed.example.com/models",
                "api_key": "",
                "model": "allowed/model",
            },
        ]),
    )

    config = GatewayConfig.from_env()

    assert config.providers[0].name == "kilo"
    assert config.providers[0].model == "dots-studio/dots-3-note-preview:free"
    assert all("gemma-4" not in item.model.casefold() for item in config.providers)
    assert any(item.name == "allowed-fallback" for item in config.providers)



def test_dots_fallback_uses_low_reasoning_effort_without_changing_other_models() -> None:
    dots = provider(
        "kilo-dots-fallback",
        "https://api.kilo.ai/api/gateway/chat/completions",
        "https://api.kilo.ai/api/gateway/models",
        "dots-studio/dots-3-note-preview:free",
    )
    laguna = provider(
        "kilo",
        "https://api.kilo.ai/api/gateway/chat/completions",
        "https://api.kilo.ai/api/gateway/models",
        "baidu/ernie-5.5-flash-preview:free",
    )

    dots_payload = AIGateway._provider_payload(
        {"messages": [{"role": "user", "content": "hi"}]},
        dots,
    )
    laguna_payload = AIGateway._provider_payload(
        {"messages": [{"role": "user", "content": "hi"}]},
        laguna,
    )

    nemotron = provider(
        "kilo-nemotron-free-fallback",
        "https://api.kilo.ai/api/gateway/chat/completions",
        "https://api.kilo.ai/api/gateway/models",
        "nvidia/nemotron-3-ultra-550b-a55b:free",
    )
    nemotron_payload = AIGateway._provider_payload(
        {"messages": [{"role": "user", "content": "hi"}]},
        nemotron,
    )

    assert dots_payload["reasoning_effort"] == "low"
    assert nemotron_payload["reasoning_effort"] == "low"
    assert "reasoning_effort" not in laguna_payload


def test_stream_visible_budget_reserves_fallback_inside_three_seconds() -> None:
    primary = provider("primary", "https://primary.test/chat", "https://primary.test/models", "primary-model")
    fallback = provider("fallback", "https://fallback.test/chat", "https://fallback.test/models", "fallback-model")
    gateway = AIGateway(
        GatewayConfig(
            providers=(primary, fallback),
            timeout_seconds=10,
            provider_timeout_seconds=10,
            first_visible_timeout_seconds=2.2,
            max_retries=0,
        )
    )

    first = gateway._stream_visible_budget(0, 2)
    second = gateway._stream_visible_budget(1, 2)
    assert first == pytest.approx(0.35)
    assert second == pytest.approx(2.20)
    assert first + second < 3.0
    assert max(0.50, first + 0.15) + second < 3.0


def test_stream_small_test_budget_remains_exact() -> None:
    primary = provider("primary", "https://primary.test/chat", "https://primary.test/models", "primary-model")
    fallback = provider("fallback", "https://fallback.test/chat", "https://fallback.test/models", "fallback-model")
    gateway = AIGateway(
        GatewayConfig(
            providers=(primary, fallback),
            timeout_seconds=2,
            provider_timeout_seconds=2,
            first_visible_timeout_seconds=0.08,
            max_retries=0,
        )
    )
    assert gateway._stream_visible_budget(0, 2) == pytest.approx(0.08)
    assert gateway._stream_visible_budget(1, 2) == pytest.approx(0.08)

def test_dots_primary_gets_free_kilo_fallback_when_no_valid_fallback_exists(monkeypatch) -> None:
    import json

    monkeypatch.setenv("AI_GATEWAY_PROVIDER", "kilo-dots-fallback")
    monkeypatch.setenv("AI_GATEWAY_URL", "https://api.kilo.ai/api/gateway/chat/completions")
    monkeypatch.setenv("AI_GATEWAY_HEALTH_URL", "https://api.kilo.ai/api/gateway/models")
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "")
    monkeypatch.setenv("AI_GATEWAY_MODEL", "dots-studio/dots-3-note-preview:free")
    monkeypatch.setenv("AI_GATEWAY_FALLBACKS_JSON", json.dumps([
        {
            "name": "forbidden-gemma",
            "url": "https://fallback.example.com/chat",
            "health_url": "https://fallback.example.com/models",
            "api_key": "",
            "model": "google/gemma-4-26b-a4b-it",
        }
    ]))

    config = GatewayConfig.from_env()

    assert len(config.providers) == 4
    assert config.providers[0].name == "kilo-dots-fallback"
    assert config.providers[0].model == "dots-studio/dots-3-note-preview:free"
    ernie = config.providers[1]
    assert ernie.name == "kilo-free-text-fallback"
    assert ernie.model == "baidu/ernie-5.5-flash-preview:free"
    assert ernie.url == config.providers[0].url
    assert ernie.health_url == config.providers[0].health_url
    nvidia = config.providers[2]
    assert nvidia.name == "kilo-nemotron-free-fallback"
    assert nvidia.model == "nvidia/nemotron-3-ultra-550b-a55b:free"
    assert nvidia.api_key == ""
    assert "Authorization" not in AIGateway._headers(nvidia)
    vl = config.providers[3]
    assert vl.name == "kilo-nemotron-vl-free-fallback"
    assert vl.model == "nvidia/nemotron-3.5-super-vl:free"
    assert vl.api_key == ""
    assert "Authorization" not in AIGateway._headers(vl)
    assert all("gemma-4" not in item.model.casefold() for item in config.providers)
    assert all("laguna" not in item.model.casefold() for item in config.providers)

def test_dots_gets_free_kilo_fallback_even_when_another_provider_is_configured(monkeypatch) -> None:
    import json

    monkeypatch.setenv("AI_GATEWAY_PROVIDER", "kilo-dots-fallback")
    monkeypatch.setenv("AI_GATEWAY_URL", "https://api.kilo.ai/api/gateway/chat/completions")
    monkeypatch.setenv("AI_GATEWAY_HEALTH_URL", "https://api.kilo.ai/api/gateway/models")
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "invalid-primary-test-token")
    monkeypatch.setenv("AI_GATEWAY_MODEL", "dots-studio/dots-3-note-preview:free")
    monkeypatch.setenv("AI_GATEWAY_FALLBACKS_JSON", json.dumps([
        {
            "name": "configured-fallback",
            "url": "https://api.kilo.ai/api/gateway/chat/completions",
            "health_url": "https://api.kilo.ai/api/gateway/models",
            "api_key": "invalid-fallback-test-token",
            "model": "some-provider/fast-model:free",
        }
    ]))

    config = GatewayConfig.from_env()

    assert len(config.providers) == 5
    assert config.providers[0].api_key == ""
    assert "Authorization" not in AIGateway._headers(config.providers[0])
    assert config.providers[1].api_key == ""
    assert "Authorization" not in AIGateway._headers(config.providers[1])
    assert [provider.name for provider in config.providers] == [
        "kilo-dots-fallback",
        "configured-fallback",
        "kilo-free-text-fallback",
        "kilo-nemotron-free-fallback",
        "kilo-nemotron-vl-free-fallback",
    ]
    assert config.providers[2].model == "baidu/ernie-5.5-flash-preview:free"
    assert config.providers[2].api_key == ""
    assert "Authorization" not in AIGateway._headers(config.providers[2])
    assert config.providers[3].model == "nvidia/nemotron-3-ultra-550b-a55b:free"
    assert config.providers[3].api_key == ""
    assert "Authorization" not in AIGateway._headers(config.providers[3])
    assert config.providers[3].url == config.providers[0].url
    assert config.providers[3].health_url == config.providers[0].health_url
    assert config.providers[4].model == "nvidia/nemotron-3.5-super-vl:free"
    assert config.providers[4].api_key == ""
    assert "Authorization" not in AIGateway._headers(config.providers[4])


def test_kilo_auto_primary_gets_text_fallback_when_none_configured(monkeypatch) -> None:
    monkeypatch.setenv("AI_GATEWAY_PROVIDER", "kilo")
    monkeypatch.setenv("AI_GATEWAY_URL", "https://api.kilo.ai/api/gateway/chat/completions")
    monkeypatch.setenv("AI_GATEWAY_HEALTH_URL", "https://api.kilo.ai/api/gateway/models")
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "invalid-primary-test-token")
    monkeypatch.setenv("AI_GATEWAY_MODEL", "kilo-auto/free")
    monkeypatch.delenv("AI_GATEWAY_FALLBACKS_JSON", raising=False)

    config = GatewayConfig.from_env()

    assert len(config.providers) == 4
    assert config.providers[0].model == "kilo-auto/free"
    assert config.providers[0].api_key == ""
    assert "Authorization" not in AIGateway._headers(config.providers[0])
    assert config.providers[1].name == "kilo-free-text-fallback"
    assert config.providers[1].model == "baidu/ernie-5.5-flash-preview:free"
    assert config.providers[1].api_key == ""
    assert "Authorization" not in AIGateway._headers(config.providers[1])
    assert config.providers[2].name == "kilo-nemotron-free-fallback"
    assert config.providers[2].model == "nvidia/nemotron-3-ultra-550b-a55b:free"
    assert config.providers[2].api_key == ""
    assert "Authorization" not in AIGateway._headers(config.providers[2])
    assert config.providers[2].url == config.providers[0].url
    assert config.providers[2].health_url == config.providers[0].health_url
    assert config.providers[3].model == "nvidia/nemotron-3.5-super-vl:free"
    assert config.providers[3].api_key == ""
    assert "Authorization" not in AIGateway._headers(config.providers[3])


def test_paid_kilo_model_keeps_configured_api_key(monkeypatch) -> None:
    monkeypatch.setenv("AI_GATEWAY_PROVIDER", "kilo")
    monkeypatch.setenv("AI_GATEWAY_URL", "https://api.kilo.ai/api/gateway/chat/completions")
    monkeypatch.setenv("AI_GATEWAY_HEALTH_URL", "https://api.kilo.ai/api/gateway/models")
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "paid-test-token")
    monkeypatch.setenv("AI_GATEWAY_MODEL", "anthropic/claude-sonnet-4.5")
    monkeypatch.delenv("AI_GATEWAY_FALLBACKS_JSON", raising=False)

    config = GatewayConfig.from_env()

    assert config.providers[0].api_key == "paid-test-token"
    assert AIGateway._headers(config.providers[0])["Authorization"] == "Bearer paid-test-token"


def test_complete_reaches_nemotron_vl_after_earlier_models_return_empty() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = __import__("json").loads(request.content)
        model = body.get("model")
        calls.append(str(model))
        if model in {"primary-model", "ernie-model", "nvidia/nemotron-3-ultra-550b-a55b:free"}:
            return httpx.Response(
                200,
                request=request,
                json={"choices": [{"message": {"role": "assistant", "content": ""}}]},
            )
        return httpx.Response(
            200,
            request=request,
            json={
                "model": model,
                "choices": [
                    {"message": {"role": "assistant", "content": "NEMOTRON_VL_SYNTHESIS_OK"}}
                ],
            },
        )

    transport = httpx.MockTransport(handler)
    config = GatewayConfig(
        providers=(
            provider("primary", "https://primary.test/chat", "https://primary.test/models", "primary-model"),
            provider("ernie", "https://ernie.test/chat", "https://ernie.test/models", "ernie-model"),
            provider("nemotron", "https://nemotron.test/chat", "https://nemotron.test/models", "nvidia/nemotron-3-ultra-550b-a55b:free"),
            provider("nemotron-vl", "https://nemotron-vl.test/chat", "https://nemotron-vl.test/models", "nvidia/nemotron-3.5-super-vl:free"),
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
        result = asyncio.run(
            gateway.complete({"messages": [{"role": "user", "content": "synthesize evidence"}]})
        )
    finally:
        httpx.AsyncClient = original

    assert result["choices"][0]["message"]["content"] == "NEMOTRON_VL_SYNTHESIS_OK"
    assert result["_deep33_gateway"]["provider"] == "nemotron-vl"
    assert calls == [
        "primary-model",
        "ernie-model",
        "nvidia/nemotron-3-ultra-550b-a55b:free",
        "nvidia/nemotron-3.5-super-vl:free",
    ]


def test_stream_reaches_nemotron_vl_after_earlier_models_return_empty() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = __import__("json").loads(request.content)
        model = body.get("model")
        calls.append(str(model))
        if model in {"primary-model", "ernie-model", "nvidia/nemotron-3-ultra-550b-a55b:free"}:
            content = b"data: [DONE]\n\n"
        else:
            content = (
                b'data: {"choices":[{"delta":{"content":"NEMOTRON_VL_STREAM_SYNTHESIS_OK"}}]}\n\n'
                b"data: [DONE]\n\n"
            )
        return httpx.Response(
            200,
            request=request,
            headers={"content-type": "text/event-stream"},
            content=content,
        )

    transport = httpx.MockTransport(handler)
    config = GatewayConfig(
        providers=(
            provider("primary", "https://primary.test/chat", "https://primary.test/models", "primary-model"),
            provider("ernie", "https://ernie.test/chat", "https://ernie.test/models", "ernie-model"),
            provider("nemotron", "https://nemotron.test/chat", "https://nemotron.test/models", "nvidia/nemotron-3-ultra-550b-a55b:free"),
            provider("nemotron-vl", "https://nemotron-vl.test/chat", "https://nemotron-vl.test/models", "nvidia/nemotron-3.5-super-vl:free"),
        ),
        timeout_seconds=2,
        max_retries=0,
        first_visible_timeout_seconds=1.0,
    )
    gateway = AIGateway(config)
    original = httpx.AsyncClient

    class Client(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    async def collect() -> bytes:
        output = bytearray()
        async for chunk in gateway.stream({"messages": [{"role": "user", "content": "synthesize evidence"}]}):
            output.extend(chunk)
        return bytes(output)

    httpx.AsyncClient = Client
    try:
        output = asyncio.run(collect())
    finally:
        httpx.AsyncClient = original

    assert calls == [
        "primary-model",
        "ernie-model",
        "nvidia/nemotron-3-ultra-550b-a55b:free",
        "nvidia/nemotron-3.5-super-vl:free",
    ]
    assert output.count(b"NEMOTRON_VL_STREAM_SYNTHESIS_OK") == 1
    assert output.count(b"data: [DONE]") == 1



def test_gateway_config_allows_longer_evidence_synthesis_budget(monkeypatch) -> None:
    monkeypatch.setenv("AI_TIMEOUT_SECONDS", "30")
    monkeypatch.setenv("AI_PROVIDER_TIMEOUT_SECONDS", "24")
    config = GatewayConfig.from_env()

    assert config.timeout_seconds == 30
    assert config.provider_timeout_seconds == 24


def test_diagnostic_inference_has_bounded_thirty_second_budget() -> None:
    gateway = AIGateway(
        GatewayConfig(
            providers=(
                provider(
                    "primary",
                    "https://primary.test/chat",
                    "https://primary.test/models",
                    "primary-model",
                ),
            ),
            timeout_seconds=30,
            provider_timeout_seconds=24,
        )
    )
    captured: dict = {}

    async def fake_complete(payload, *, request_id=None, idempotency_key=None, deadline=None, prefer_ttft=False):
        captured["payload"] = payload
        captured["deadline"] = deadline
        captured["started"] = time.monotonic()
        return {
            "choices": [
                {"message": {"role": "assistant", "content": "DEEP33_DIAGNOSTIC_OK"}}
            ]
        }

    gateway.complete = fake_complete
    before = time.monotonic()
    result = asyncio.run(gateway.diagnostic_inference(request_id="budget-test"))
    budget = captured["deadline"] - before

    assert budget <= 30.1
    assert budget >= 29.0
    assert result["choices"][0]["message"]["content"] == "DEEP33_DIAGNOSTIC_OK"



def test_complete_timeout_budget_prioritizes_fallback_provider() -> None:
    gateway = AIGateway(
        GatewayConfig(
            providers=(
                provider("primary", "https://primary.test/chat", "https://primary.test/models", "primary-model"),
                provider("kilo-free-text-fallback", "https://fallback.test/chat", "https://fallback.test/models", "baidu/ernie-5.5-flash-preview:free"),
            ),
            timeout_seconds=30,
            provider_timeout_seconds=24,
        )
    )
    primary_deadline = time.monotonic() + 26.0
    primary_budget = gateway._complete_provider_timeout_budget(
        primary_deadline, gateway.config.providers[0], 0, list(gateway.config.providers)
    )
    fallback_deadline = time.monotonic() + 16.7
    fallback_budget = gateway._complete_provider_timeout_budget(
        fallback_deadline, gateway.config.providers[1], 1, list(gateway.config.providers)
    )

    assert 9.0 <= primary_budget <= 9.5
    assert 16.5 <= fallback_budget <= 17.0



def test_complete_timeout_budget_prioritizes_text_fallback_in_four_provider_chain() -> None:
    providers = (
        provider("primary", "https://primary.test/chat", "https://primary.test/models", "primary-model"),
        provider("kilo-free-text-fallback", "https://ernie.test/chat", "https://ernie.test/models", "baidu/ernie-5.5-flash-preview:free"),
        provider("nemotron", "https://nemotron.test/chat", "https://nemotron.test/models", "nvidia/nemotron-3-ultra-550b-a55b:free"),
        provider("nemotron-vl", "https://nemotron-vl.test/chat", "https://nemotron-vl.test/models", "nvidia/nemotron-3.5-super-vl:free"),
    )
    gateway = AIGateway(
        GatewayConfig(
            providers=providers,
            timeout_seconds=30,
            provider_timeout_seconds=30,
        )
    )

    remaining = 30.0
    budgets = []
    for index in range(len(providers)):
        deadline = time.monotonic() + remaining
        budget = gateway._complete_provider_timeout_budget(
            deadline,
            providers[index],
            index,
            list(providers),
        )
        budgets.append(budget)
        remaining -= budget

    assert len(budgets) == 4
    assert 7.0 <= budgets[0] <= 8.0
    assert 13.0 <= budgets[1] <= 14.0
    assert 4.0 <= budgets[2] <= 5.0
    assert 4.0 <= budgets[3] <= 5.0



def test_generic_fallback_does_not_receive_ernie_text_priority() -> None:
    providers = (
        provider("primary", "https://primary.test/chat", "https://primary.test/models", "primary-model"),
        provider("fallback", "https://fallback.test/chat", "https://fallback.test/models", "fallback-model"),
        provider("secondary", "https://secondary.test/chat", "https://secondary.test/models", "secondary-model"),
    )
    gateway = AIGateway(
        GatewayConfig(
            providers=providers,
            timeout_seconds=30,
            provider_timeout_seconds=30,
        )
    )
    deadline = time.monotonic() + 30.0

    primary_budget = gateway._complete_provider_timeout_budget(
        deadline, providers[0], 0, list(providers)
    )
    fallback_deadline = time.monotonic() + max(0.0, 30.0 - primary_budget)
    generic_fallback_budget = gateway._complete_provider_timeout_budget(
        fallback_deadline, providers[1], 1, list(providers)
    )

    assert primary_budget > generic_fallback_budget
