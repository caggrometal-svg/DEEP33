from fastapi.testclient import TestClient

from backend import main

client = TestClient(main.app)


def fake_network_probe() -> dict:
    return {
        "internet_available": True,
        "dns_ok": True,
        "https_ok": True,
        "latency_ms": 1.0,
        "timestamp": "2026-01-01T00:00:00+00:00",
        "error": None,
    }


def fake_gateway_probe() -> dict:
    return {
        "gateway": "PASS",
        "provider": "kilo",
        "model": "kilo-auto/small",
        "latency_ms": 10.0,
        "last_success": "2026-01-01T00:00:00+00:00",
        "last_error": None,
    }


def fake_call_gateway(payload: dict) -> dict:
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
    }


def test_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "PASS"


def test_ready() -> None:
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
    ):
        assert key in body
    assert body["gateway"] == "PASS"
    assert body["provider"] == "kilo"


def test_ai_diagnostics_contract(monkeypatch) -> None:
    monkeypatch.setattr(main, "network_probe", fake_network_probe)
    monkeypatch.setattr(main, "gateway_probe", fake_gateway_probe)
    response = client.get("/v1/ai/diagnostics")
    assert response.status_code == 200
    body = response.json()
    assert body["checks"]["BACKEND"] == "PASS"
    assert body["checks"]["NETWORK"] == "PASS"
    assert body["checks"]["AI_GATEWAY"] == "PASS"
    assert body["checks"]["MODEL"] == "PASS"
    assert body["online"] is True


def test_chat_contract(monkeypatch) -> None:
    monkeypatch.setattr(main, "call_gateway", fake_call_gateway)
    response = client.post(
        "/v1/chat",
        json={"messages": [{"role": "user", "content": "Hola"}]},
    )
    assert response.status_code == 200
    body = response.json()
    assert (
        body["response"]["choices"][0]["message"]["content"]
        == "DEEP33 connectivity PASS"
    )
