from fastapi.testclient import TestClient

from backend.main import app

client = TestClient(app)


def test_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "PASS"


def test_ready() -> None:
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json()["ready"] is True


def test_network_contract() -> None:
    response = client.get("/v1/network/status")
    assert response.status_code == 200
    body = response.json()
    for key in ("internet_available", "backend_reachable", "dns_ok", "https_ok", "latency_ms", "timestamp"):
        assert key in body


def test_ai_status_contract() -> None:
    response = client.get("/v1/ai/status")
    assert response.status_code == 200
    body = response.json()
    for key in ("gateway", "provider", "model", "latency_ms", "last_success", "last_error"):
        assert key in body


def test_ai_diagnostics_contract() -> None:
    response = client.get("/v1/ai/diagnostics")
    assert response.status_code == 200
    body = response.json()
    assert "checks" in body
    assert "online" in body
    assert body["checks"]["BACKEND"] == "PASS"
