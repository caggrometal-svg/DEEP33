from fastapi.testclient import TestClient

from backend import main

client = TestClient(main.app)


class FakeMemory:
    enabled = True

    async def ping(self) -> dict:
        return {"ok": True}

    async def context(self, session_id: str, memory_profile_id=None, access_token=None) -> dict:
        return {"session": {"session_id": session_id}, "messages": [], "memories": []}

    async def sync(self, *args, **kwargs) -> dict:
        return {"ok": True}

    async def remember(self, *args, **kwargs) -> dict:
        return {"ok": True}

    async def set_preferences(self, *args, **kwargs) -> dict:
        return {"ok": True}

    async def idempotency_claim(self, *args, **kwargs) -> dict:
        return {"state": "CLAIMED", "lease_token": "test-lease"}

    async def idempotency_status(self, *args, **kwargs) -> dict:
        return {"state": "IN_PROGRESS"}

    async def idempotency_complete(self, *args, **kwargs) -> dict:
        return {"ok": True}

    async def idempotency_fail(self, *args, **kwargs) -> dict:
        return {"ok": True}


class UnavailableIdempotencyMemory(FakeMemory):
    async def idempotency_claim(self, *args, **kwargs) -> dict:
        raise main.MemoryUnavailableError("memory_http_503")


def patch_memory(monkeypatch) -> None:
    monkeypatch.setattr(main, "memory", FakeMemory())


async def fake_network_probe() -> dict:
    return {
        "internet_available": True,
        "dns_ok": True,
        "https_ok": True,
        "latency_ms": 1.0,
        "timestamp": "2026-01-01T00:00:00+00:00",
        "error": None,
    }


async def fake_gateway_probe() -> dict:
    return {
        "gateway": "PASS",
        "provider": "kilo",
        "model": "kilo-auto/small",
        "latency_ms": 10.0,
        "last_success": "2026-01-01T00:00:00+00:00",
        "last_error": None,
        "fallback_used": False,
    }


async def fake_call_gateway(payload: dict, **_kwargs) -> dict:
    return {
        "id": "test",
        "model": payload["model"],
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "DEEP33 connectivity PASS",
                },
                "finish_reason": "stop",
            }
        ],
        "_deep33_gateway": {
            "provider": "kilo",
            "model": payload["model"],
        },
    }


def test_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "PASS"


def test_ready(monkeypatch) -> None:
    patch_memory(monkeypatch)
    monkeypatch.setattr(main, "network_probe", fake_network_probe)
    monkeypatch.setattr(main, "gateway_probe", fake_gateway_probe)
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json()["ready"] is True


def test_network_contract(monkeypatch) -> None:
    monkeypatch.setattr(main, "network_probe", fake_network_probe)
    response = client.get("/v1/network/status")
    assert response.status_code == 200
    body = response.json()
    for key in (
        "internet_available",
        "backend_reachable",
        "dns_ok",
        "https_ok",
        "latency_ms",
        "timestamp",
    ):
        assert key in body
    assert body["internet_available"] is True


def test_ai_status_contract(monkeypatch) -> None:
    monkeypatch.setattr(main, "gateway_probe", fake_gateway_probe)
    response = client.get("/v1/ai/status")
    assert response.status_code == 200
    body = response.json()
    for key in (
        "gateway",
        "provider",
        "model",
        "latency_ms",
        "last_success",
        "last_error",
        "fallback_used",
    ):
        assert key in body
    assert body["gateway"] == "PASS"
    assert body["provider"] == "kilo"


async def fake_inference_check(request_id: str):
    return "PASS", {"_deep33_gateway": {"provider": "kilo", "model": "test-model"}}, "DEEP33_DIAGNOSTIC_OK"


def test_ai_diagnostics_contract(monkeypatch) -> None:
    monkeypatch.setattr(main, "network_probe", fake_network_probe)
    monkeypatch.setattr(main, "gateway_probe", fake_gateway_probe)
    monkeypatch.setattr(main, "run_inference_check", fake_inference_check)
    response = client.get("/v1/ai/diagnostics")
    assert response.status_code == 200
    body = response.json()
    assert body["checks"]["BACKEND"] == "PASS"
    assert body["checks"]["NETWORK"] == "PASS"
    assert body["checks"]["AI_GATEWAY"] == "PASS"
    assert body["checks"]["MODEL"] == "PASS"
    assert body["online"] is True


def test_chat_contract(monkeypatch) -> None:
    patch_memory(monkeypatch)
    monkeypatch.setattr(main, "call_gateway", fake_call_gateway)
    response = client.post(
        "/v1/chat",
        json={"messages": [{"role": "user", "content": "Hola"}]},
        headers={"X-DEEP33-Session-Id": "test-session"},
    )
    assert response.status_code == 200
    body = response.json()
    assert (
        body["response"]["choices"][0]["message"]["content"]
        == "DEEP33 connectivity PASS"
    )
    assert body["result"]["text"] == "DEEP33 connectivity PASS"


def test_stream_contract_hides_sources(monkeypatch) -> None:
    patch_memory(monkeypatch)

    async def fake_prepare_web_evidence(messages, request_id=None, deep=False):
        return (
            [
                {"role": "system", "content": "Server-side web evidence"},
            ],
            [{"title": "Trusted evidence", "url": "https://trusted.example.org/page/"}],
            ["evidence"],
            [{"title": "Trusted evidence", "url": "https://trusted.example.org/page/", "snippet": "evidence"}],
        )

    monkeypatch.setattr(main, "prepare_web_evidence", fake_prepare_web_evidence)

    async def fake_gateway_stream(*_args, **_kwargs):
        yield (
            b'data: {"choices":[{"delta":{"content":"Respuesta propia de DEEP33.\\n\\nFuentes: https://example.com"}}]}\n\n'
        )
        yield b"data: [DONE]\n\n"

    monkeypatch.setattr(main.gateway, "stream", fake_gateway_stream)
    with client.stream(
        "POST",
        "/v1/chat/stream",
        json={
            "messages": [{"role": "user", "content": "Busca y responde"}],
            "personality": "NEUTRO",
        },
        headers={
            "X-DEEP33-Session-Id": "stream-source-sanitization",
            "X-Idempotency-Key": "stream-source-sanitization-key",
        },
    ) as response:
        body = "".join(response.iter_text())

    assert response.status_code == 200
    assert "Respuesta propia de DEEP33." in body
    assert "example.com" not in body
    assert "https://trusted.example.org/page/" in body
    assert "Fuentes consultadas:" in body
    assert "turn1search1" not in body
    assert "Fuentes:" not in body


def test_stream_gateway_emits_first_delta_before_completion(monkeypatch) -> None:
    import asyncio

    async def fake_gateway_stream(*_args, **_kwargs):
        yield b'data: {"choices":[{"delta":{"content":"Primer token"}}]}\n\n'
        yield b'data: {"choices":[{"delta":{"content":" final"}}]}\n\n'
        yield b"data: [DONE]\n\n"

    monkeypatch.setattr(main.gateway, "stream", fake_gateway_stream)

    async def run() -> None:
        request_id = "phase1-first-delta"
        completion_state: dict[str, str] = {}
        stream = main.stream_gateway(
            {"messages": [{"role": "user", "content": "Hola"}], "model": "test-model"},
            "phase1-session",
            "NEUTRO",
            request_id=request_id,
            idempotency_key="phase1-key",
            request_hash="phase1-hash",
            lease_token="phase1-lease",
            completion_state=completion_state,
        )

        first = await stream.__anext__()
        assert b"Primer token" in first
        assert completion_state == {}
        assert main.performance.trace_snapshot(request_id)["stages"].get("T7_FIRST_TOKEN") is not None

        second = await stream.__anext__()
        assert b"final" in second
        await stream.__anext__()
        assert completion_state["assistant_text"] == "Primer token final"

    asyncio.run(run())


def test_generate_contract(monkeypatch) -> None:
    patch_memory(monkeypatch)
    monkeypatch.setattr(main, "call_gateway", fake_call_gateway)
    response = client.post(
        "/v1/ai/generate",
        json={"messages": [{"role": "user", "content": "Hola DEEP33"}]},
        headers={"X-DEEP33-Session-Id": "test-session"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["request_id"]
    assert body["result"]["role"] == "assistant"
    assert body["result"]["provider"] == "kilo"
    assert body["result"]["text"] == "DEEP33 connectivity PASS"


def test_generate_falls_back_to_local_idempotency_when_memory_store_is_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(main, "memory", UnavailableIdempotencyMemory())
    calls = {"count": 0}

    async def counting_gateway(payload: dict, **_kwargs) -> dict:
        calls["count"] += 1
        return await fake_call_gateway(payload)

    monkeypatch.setattr(main, "call_gateway", counting_gateway)

    headers = {
        "X-DEEP33-Session-Id": "local-idempotency-fallback",
        "X-Idempotency-Key": "local-idempotency-key",
    }
    payload = {"messages": [{"role": "user", "content": "Hola"}]}

    first = client.post("/v1/ai/generate", json=payload, headers=headers)
    second = client.post("/v1/ai/generate", json=payload, headers=headers)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["result"]["text"] == "DEEP33 connectivity PASS"
    assert second.json()["result"]["text"] == "DEEP33 connectivity PASS"
    assert calls["count"] == 1


def test_personality_is_injected_into_model_context(monkeypatch) -> None:
    patch_memory(monkeypatch)
    captured: dict = {}

    async def capture_gateway(payload: dict, **_kwargs) -> dict:
        captured["messages"] = payload["messages"]
        return await fake_call_gateway(payload)

    monkeypatch.setattr(main, "call_gateway", capture_gateway)

    for name in main.PERSONALITIES:
        response = client.post(
            "/v1/ai/generate",
            json={
                "personality": name,
                "messages": [{"role": "user", "content": "Hola"}],
            },
            headers={"X-DEEP33-Session-Id": "personality-" + name.lower()},
        )
        assert response.status_code == 200
        assert response.json()["result"]["personality"] == name
        system = captured["messages"][0]
        assert system["role"] == "system"
        assert name in system["content"]
        assert main.PERSONALITIES[name]["instruction"] in system["content"]


def test_personality_catalog() -> None:
    response = client.get("/v1/personalities")
    assert response.status_code == 200
    assert [p["name"] for p in response.json()["personalities"]] == [
        "AGRESIVO",
        "NEUTRO",
        "COMICO",
        "CONSPIRANOICO",
    ]

def test_chat_stream_honors_skip_web_tools_header(monkeypatch) -> None:
    patch_memory(monkeypatch)
    monkeypatch.setattr(main, "DEEP33_WEB_TOOLS_ENABLED", True)

    async def fake_prepare_messages(request, session_id, memory_profile_id=None, request_id=None, access_token=None):
        return ([{"role": "user", "content": "noticias de hoy"}], "NEUTRO")

    monkeypatch.setattr(main, "prepare_messages", fake_prepare_messages)
    web_called = {"value": False}

    async def reject_nested_web_search(*args, **kwargs):
        web_called["value"] = True
        raise AssertionError("Render fallback must not start a second web search")

    monkeypatch.setattr(main, "prepare_web_evidence", reject_nested_web_search)

    async def fake_stream_gateway(payload, session_id, personality, **kwargs):
        completion_state = kwargs.get("completion_state")
        if completion_state is not None:
            completion_state["assistant_text"] = "Respuesta directa"
        yield b'data: {"choices":[{"delta":{"content":"Respuesta directa"}}]}\n\n'
        yield b"data: [DONE]\n\n"

    monkeypatch.setattr(main, "stream_gateway", fake_stream_gateway)
    response = client.post(
        "/v1/chat/stream",
        json={"messages": [{"role": "user", "content": "noticias de hoy"}]},
        headers={
            "X-DEEP33-Session-Id": "test-stream-skip-web",
            "X-DEEP33-Skip-Web-Tools": "true",
            "X-Request-ID": "test-stream-skip-web-request",
        },
    )

    assert response.status_code == 200
    assert "data: [DONE]" in response.text
    assert web_called["value"] is False



def test_web_search_result_limit_expands_only_for_realtime_chile_news() -> None:
    from backend.main import _web_search_result_limit

    assert _web_search_result_limit("política Chile hoy últimas noticias", 5) == 8
    assert _web_search_result_limit("sitio gubernamental Chile información pública", 5) == 5
    assert _web_search_result_limit("Python asyncio documentation", 5) == 5
    assert _web_search_result_limit("política Chile hoy últimas noticias", 8) == 8


def test_generate_claim_failure_preserves_original_error_without_a_lease(monkeypatch) -> None:
    import asyncio
    import pytest

    cancelled = asyncio.Event()

    async def failed_claim(*args, **kwargs):
        await asyncio.sleep(0)
        raise RuntimeError("original claim failure")

    async def pending_context(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(main, "cache_get", lambda *args, **kwargs: None)
    monkeypatch.setattr(main, "local_idempotency_get", lambda *args, **kwargs: None)
    monkeypatch.setattr(main, "shared_idempotency_claim", failed_claim)
    monkeypatch.setattr(main, "prepare_messages", pending_context)
    monkeypatch.setattr(main, "DEEP33_WEB_TOOLS_ENABLED", False)
    monkeypatch.setattr(main, "memory", FakeMemory())

    request = main.ChatRequest(
        messages=[main.ChatMessage(role="user", content="hola")]
    )

    async def exercise():
        with pytest.raises(RuntimeError, match="original claim failure"):
            await main.generate(
                request,
                "test-session",
                request_id="idempotency-claim-failure-test",
                idempotency_key="test-idempotency-key",
            )
        await asyncio.wait_for(cancelled.wait(), timeout=0.5)

    asyncio.run(exercise())



def test_google_news_wrapper_resolves_to_readable_publisher_article(monkeypatch):
    import asyncio
    from backend import main

    original = "https://news.google.com/rss/articles/opaque-token"
    publisher_url = "https://www.emol.com/noticias/chile/article.html"
    fetched = []

    async def fake_search(query, **_kwargs):
        assert "emol.com" in query
        assert "Noticia de Chile" in query
        return {
            "ok": True,
            "results": [{
                "title": "Noticia de Chile",
                "url": publisher_url,
                "provider": "bing",
            }],
        }

    async def fake_fetch(url, **_kwargs):
        fetched.append(url)
        return {
            "ok": True,
            "url": url,
            "final_url": url,
            "title": "Noticia de Chile",
            "text": "Texto extraído del artículo de Emol. " * 12,
            "retrieved_at": "2026-10-16T00:00:00+00:00",
        }

    monkeypatch.setattr(main, "search_web", fake_search)
    monkeypatch.setattr(main, "fetch_page", fake_fetch)
    result = asyncio.run(main._fetch_web_source(
        {
            "title": "Noticia de Chile",
            "url": original,
            "source_domain": "www.emol.com",
            "provider": "google_news",
        },
        request_id="google-news-rescue-test",
        timeout_seconds=2.5,
    ))

    assert result is not None
    assert result["url"] == publisher_url
    assert fetched == [publisher_url]




def test_google_news_wrapper_retries_with_site_keywords_when_exact_title_misses(monkeypatch):
    import asyncio
    from backend import main

    original = "https://news.google.com/rss/articles/opaque-token-2"
    publisher_url = "https://www.pauta.cl/actualidad/2026/10/16/dolar-chile.html"
    queries = []

    async def fake_search(query, **_kwargs):
        queries.append(query)
        if query.startswith("site:pauta.cl"):
            return {
                "ok": True,
                "results": [{
                    "title": "Dólar en Chile precio y valor de la divisa",
                    "url": publisher_url,
                    "provider": "bing",
                }],
            }
        return {"ok": True, "results": []}

    async def fake_fetch(url, **_kwargs):
        return {
            "ok": True,
            "url": url,
            "final_url": url,
            "title": "Dólar en Chile",
            "text": "Texto verificable del artículo original de Pauta. " * 12,
            "retrieved_at": "2026-10-16T00:00:00+00:00",
        }

    monkeypatch.setattr(main, "search_web", fake_search)
    monkeypatch.setattr(main, "fetch_page", fake_fetch)
    result = asyncio.run(main._fetch_web_source(
        {
            "title": "Dólar en Chile: precio y valor de la divisa hoy jueves",
            "url": original,
            "source_domain": "pauta.cl",
            "provider": "google_news",
        },
        request_id="google-news-keyword-rescue-test",
        timeout_seconds=2.5,
    ))

    assert result is not None
    assert result["url"] == publisher_url
    assert len(queries) == 2
    assert queries[1].startswith("site:pauta.cl ")


def test_inference_check_does_not_pass_on_unrelated_nonempty_model_text(monkeypatch):
    import asyncio
    from backend import main

    calls = 0

    async def fake_diagnostic_inference(**_kwargs):
        nonlocal calls
        calls += 1
        return {
            "model": "test-model",
            "_deep33_gateway": {"provider": "test-provider", "model": "test-model"},
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": "DEEP33 reports inference status without the diagnostic sentinel"},
                "finish_reason": "stop",
            }],
        }

    monkeypatch.setattr(main.gateway, "diagnostic_inference", fake_diagnostic_inference)
    status, _data, text_value = asyncio.run(main.run_inference_check("test-inference-check"))

    assert status == "FAIL"
    assert "diagnostic sentinel" in text_value
    assert calls == 2


def test_diagnostic_inference_uses_a_short_deterministic_completion(monkeypatch):
    import asyncio
    from backend.gateway import AIGateway

    gateway = AIGateway()
    seen = {}

    async def fake_complete(payload, **kwargs):
        seen["payload"] = payload
        seen["kwargs"] = kwargs
        return {
            "model": "test-model",
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": "DEEP33_DIAGNOSTIC_OK"},
                "finish_reason": "stop",
            }],
        }

    monkeypatch.setattr(gateway, "complete", fake_complete)
    result = asyncio.run(gateway.diagnostic_inference(
        request_id="short-diagnostic-test",
        deadline=__import__("time").monotonic() + 2.0,
    ))

    assert result["choices"][0]["message"]["content"] == "DEEP33_DIAGNOSTIC_OK"
    assert seen["payload"]["max_tokens"] == 128
    assert seen["kwargs"]["deadline"] <= __import__("time").monotonic() + 8.1
    assert seen["payload"]["temperature"] == 0.0
    assert seen["kwargs"]["prefer_ttft"] is True


def test_web_search_endpoint_preserves_editorial_domain_and_returns_resolved_url(monkeypatch):
    import asyncio
    from backend import main

    wrapper = "https://news.google.com/rss/articles/opaque-token"
    direct = "https://www.emol.com/noticias/Nacional/2026/10/16/article.html"
    cooperativa = "https://www.cooperativa.cl/noticias/pais/politica/article.html"

    async def allow_controls(*_args, **_kwargs):
        return None

    async def fake_search(_query, **_kwargs):
        return {
            "ok": True,
            "results": [
                {
                    "title": "Últimas noticias de política en Chile hoy: Gobierno y Congreso",
                    "url": wrapper,
                    "source_domain": "www.emol.com",
                    "provider": "google_news",
                    "snippet": "Últimas noticias de política en Chile hoy: Gobierno y Congreso avanzan en la agenda nacional.",
                },
                {
                    "title": "Noticias de política de Chile hoy: Senado debate nueva reforma",
                    "url": cooperativa,
                    "source_domain": "www.cooperativa.cl",
                    "provider": "bing",
                    "snippet": "Últimas noticias políticas de Chile hoy: el Senado inició el debate de una reforma del Gobierno.",
                },
            ],
            "sources": [],
            "errors": [],
            "providers": ["google_news", "bing"],
            "providers_attempted": ["google_news", "bing"],
            "winner_provider": "multi",
        }

    async def fake_fetch_source(source, **_kwargs):
        assert source.get("source_domain") in {"emol.com", "cooperativa.cl"}
        url = direct if source["source_domain"] == "emol.com" else cooperativa
        return {
            "title": source["title"],
            "url": url,
            "text": "Texto editorial verificable de la fuente original. " * 8,
            "provider": source["provider"],
            "domain": source["source_domain"],
            "retrieved_at": "2026-10-16T02:00:00+00:00",
            "text_chars": 360,
        }

    monkeypatch.setattr(main, "enforce_client_controls", allow_controls)
    monkeypatch.setattr(main, "search_web", fake_search)
    monkeypatch.setattr(main, "_fetch_web_source", fake_fetch_source)

    response = client.get(
        "/v1/web/search",
        params={"q": "política Chile hoy últimas noticias"},
        headers={"X-DEEP33-Session-Id": "test-web-search-resolution"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["domains_used"] == 2
    assert body["results"][0]["url"] == direct
    assert body["results"][0]["original_url"] == wrapper
    assert body["results"][0]["source_domain"] == "www.emol.com"




def test_web_search_endpoint_prioritizes_direct_publishers_within_edge_budget(monkeypatch):
    import time
    from backend import main

    wrapper = "https://news.google.com/rss/articles/opaque-slow"
    direct_a = "https://www.cooperativa.cl/noticias/pais/a.html"
    direct_b = "https://www.biobiochile.cl/noticias/nacional/b.shtml"
    fetched = []

    async def allow_controls(*_args, **_kwargs):
        return None

    async def fake_search(_query, **kwargs):
        assert 3.0 <= kwargs["timeout_seconds"] <= 5.0
        return {
            "ok": True,
            "results": [
                {"title": "Últimas noticias de Chile hoy: política", "url": wrapper, "source_domain": "emol.com", "provider": "google_news", "snippet": "Últimas noticias de Chile hoy y actualidad nacional."},
                {"title": "Noticias de Chile hoy: informe nacional", "url": direct_a, "source_domain": "cooperativa.cl", "provider": "bing", "snippet": "Últimas noticias e información actual de Chile hoy."},
                {"title": "Actualidad nacional de Chile hoy", "url": direct_b, "source_domain": "biobiochile.cl", "provider": "duckduckgo", "snippet": "Noticias de Chile hoy con la actualidad nacional."},
            ],
            "errors": [],
        }

    async def fake_fetch_source(source, **kwargs):
        fetched.append(source["url"])
        assert 3.3 <= kwargs["timeout_seconds"] <= 4.0
        assert kwargs["direct_timeout_seconds"] == 0.85
        return {
            "title": source["title"],
            "url": source["url"],
            "text": "Texto editorial directo verificable. " * 8,
            "provider": source["provider"],
            "domain": source["source_domain"],
            "retrieved_at": "2026-10-16T03:00:00+00:00",
            "text_chars": 280,
        }

    monkeypatch.setattr(main, "enforce_client_controls", allow_controls)
    monkeypatch.setattr(main, "search_web", fake_search)
    monkeypatch.setattr(main, "_fetch_web_source", fake_fetch_source)

    started = time.perf_counter()
    response = client.get(
        "/v1/web/search",
        params={"q": "noticias Chile hoy"},
        headers={"X-DEEP33-Session-Id": "edge-budget-test"},
    )
    elapsed = time.perf_counter() - started
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["domains_used"] == 2
    assert fetched == [direct_a, direct_b]
    assert elapsed < 0.5


def test_editorial_domain_dedup_normalizes_www_prefix():
    from backend.search.engine import deduplicate

    ranked = [
        {
            "title": "Primera noticia política",
            "url": "https://news.google.com/rss/articles/a",
            "source_domain": "www.emol.com",
            "snippet": "Gobierno y Congreso de Chile.",
        },
        {
            "title": "Segunda noticia política",
            "url": "https://news.google.com/rss/articles/b",
            "source_domain": "emol.com",
            "snippet": "Senado y política nacional.",
        },
        {
            "title": "Tercera noticia política",
            "url": "https://www.cooperativa.cl/noticias/pais/politica/c",
            "source_domain": "www.cooperativa.cl",
            "snippet": "Gobierno y oposición en Chile.",
        },
    ]
    selected = deduplicate(ranked, 2)
    domains = {
        str(item.get("source_domain") or "").removeprefix("www.")
        for item in selected
    }
    assert domains == {"emol.com", "cooperativa.cl"}

def test_web_fetch_endpoint_reserves_reader_budget_under_edge_deadline(monkeypatch):
    calls = []

    async def allow_controls(*_args, **_kwargs):
        return None

    async def fake_fetch(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return {
            "ok": True,
            "url": url,
            "final_url": url,
            "title": "Current publisher article",
            "text": "EXTRACTED_PAGE_TEXT " + ("Verified publisher article text. " * 8),
            "retrieved_at": "2026-10-18T06:00:00+00:00",
            "redirects": 0,
        }

    monkeypatch.setattr(main, "enforce_client_controls", allow_controls)
    monkeypatch.setattr(main, "fetch_page", fake_fetch)
    monkeypatch.setattr(main, "DEEP33_WEB_TOOLS_ENABLED", True)

    response = client.get(
        "/v1/web/fetch",
        params={"url": "https://publisher.example/current-article"},
        headers={
            "X-DEEP33-Session-Id": "fetch-reader-budget-test",
            "X-Request-ID": "fetch-reader-budget-test",
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["ok"] is True
    assert payload["status"] == "success"
    assert payload["text_chars"] >= 80
    assert "EXTRACTED_PAGE_TEXT" in payload["text"]
    assert len(calls) == 1
    assert 3.3 <= calls[0]["timeout_seconds"] <= 4.0
    assert calls[0]["direct_timeout_seconds"] == 0.85

def test_web_search_rejects_unrelated_direct_pages_for_relevant_news_wrapper(monkeypatch):
    wrapper = "https://news.google.com/rss/articles/relevant-current-news"
    irrelevant_direct = "https://www.banmedica.cl/sucursal-virtual"
    irrelevant_login = "https://login.isaprebanmedica.cl/login"
    fetched = []

    async def allow_controls(*_args, **_kwargs):
        return None

    async def fake_search(_query, **_kwargs):
        return {
            "ok": True,
            "results": [
                {
                    "title": "Sucursal virtual Banmédica",
                    "url": irrelevant_direct,
                    "source_domain": "banmedica.cl",
                    "provider": "bing",
                    "snippet": "Ingresa a tu cuenta de salud y consulta tus servicios.",
                },
                {
                    "title": "Login Isapre Banmédica",
                    "url": irrelevant_login,
                    "source_domain": "login.isaprebanmedica.cl",
                    "provider": "bing",
                    "snippet": "Acceso a consultas, cuenta y servicios.",
                },
                {
                    "title": "Últimas noticias de Chile hoy: política y actualidad",
                    "url": wrapper,
                    "source_domain": "emol.com",
                    "provider": "google_news",
                    "snippet": "Últimas noticias de Chile hoy con cobertura de política y actualidad nacional.",
                },
            ],
            "sources": [],
            "errors": [],
            "providers": ["bing", "google_news"],
            "providers_attempted": ["bing", "google_news"],
            "winner_provider": "multi",
        }

    async def fake_fetch_source(source, **kwargs):
        fetched.append(source["url"])
        assert source["url"] == wrapper
        assert source["source_domain"] == "emol.com"
        return {
            "title": source["title"],
            "url": "https://www.emol.com/noticias/Nacional/2026/10/18/noticia.html",
            "text": "Texto editorial de noticias de Chile hoy. " * 10,
            "provider": source["provider"],
            "domain": "emol.com",
            "retrieved_at": "2026-10-18T06:00:00+00:00",
            "text_chars": 390,
        }

    monkeypatch.setattr(main, "enforce_client_controls", allow_controls)
    monkeypatch.setattr(main, "search_web", fake_search)
    monkeypatch.setattr(main, "_fetch_web_source", fake_fetch_source)

    response = client.get(
        "/v1/web/search",
        params={"q": "últimas noticias de Chile hoy"},
        headers={"X-DEEP33-Session-Id": "relevance-filter-test"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["domains_used"] == 1
    assert body["fetched_pages"][0]["url"].startswith("https://www.emol.com/")
    assert fetched == [wrapper]
    assert all(
        "banmedica" not in item["url"].casefold()
        for item in body["results"]
    )
