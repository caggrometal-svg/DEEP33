from fastapi.testclient import TestClient

from backend import main

client = TestClient(main.app)


class UnavailableIdempotencyMemory(FakeMemory):
    async def idempotency_claim(self, *args, **kwargs) -> dict:
        raise main.MemoryUnavailableError("memory_http_503")


class FakeMemory:
    enabled = True

    async def ping(self) -> dict:
        return {"ok": True}

    async def context(self, session_id: str) -> dict:
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

    for name, phrase in {
        "AGRESIVO": "directa, firme y provocadora",
        "NEUTRO": "equilibrada, profesional, natural y clara",
        "COMICO": "humor, ironía y ocurrencias breves",
        "CONSPIRANOICO": "enigmático y tecnológico",
    }.items():
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
        assert phrase in system["content"]


def test_personality_catalog() -> None:
    response = client.get("/v1/personalities")
    assert response.status_code == 200
    assert [p["name"] for p in response.json()["personalities"]] == [
        "AGRESIVO",
        "NEUTRO",
        "COMICO",
        "CONSPIRANOICO",
    ]
