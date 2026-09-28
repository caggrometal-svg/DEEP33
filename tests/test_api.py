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
    for key in ("internet_available", "backend_reachable", "dns_ok", "https_ok", "latency_ms", "timestamp"):
        assert key in body
    assert body["internet_available"] is True


def test_ai_status_contract() -> None:
    response = client.get("/v1/ai/status")
    assert response.status_code == 200
    body = response.json()
    for key in ("gateway", "provider", "model", "latency_ms", "last_success", "last_error"):
        assert key in body
    assert body["gateway"] == "NOT_VERIFIED"


def test_ai_diagnostics_contract(monkeypatch) -> None:
    monkeypatch.setattr(main, "network_probe", fake_network_probe)
    response = client.get("/v1/ai/diagnostics")
    assert response.status_code == 200
    body = response.json()
    assert "checks" in body
    assert "online" in body
    assert body["checks"]["BACKEND"] == "PASS"
    assert body["checks"]["NETWORK"] == "PASS"
    assert body["online"] is False
