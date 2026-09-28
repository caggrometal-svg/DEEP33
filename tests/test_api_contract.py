from __future__ import annotations

from fastapi.testclient import TestClient

from backend.main import app


def test_generation_requires_session_id() -> None:
    client = TestClient(app)
    response = client.post(
        "/v1/ai/generate",
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "DEEP33_SESSION_ID_REQUIRED"


def test_health_contract_is_stable() -> None:
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "PASS"
    assert body["service"] == "DEEP33 Backend"
    assert body["version"] == "0.2.0"
