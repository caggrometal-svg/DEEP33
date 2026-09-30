from __future__ import annotations

from backend.main import DEFAULT_PERSONALITY, PERSONALITIES, normalize_personality, personality_prompt


def test_personality_catalog_has_required_profiles() -> None:
    assert set(PERSONALITIES) == {"AGRESIVO", "NEUTRO", "COMICO", "CONSPIRANOICO"}
    assert DEFAULT_PERSONALITY == "NEUTRO"


def test_invalid_personality_falls_back_to_neutral() -> None:
    assert normalize_personality("") == "NEUTRO"
    assert normalize_personality("desconocido") == "NEUTRO"


def test_personality_prompt_is_style_only() -> None:
    conspiranoic = personality_prompt("CONSPIRANOICO")
    assert "CONSPIRANOICO" in conspiranoic
    assert "active personality contract" in conspiranoic.lower()
    assert "must not silently fall back to NEUTRO" in conspiranoic
    assert "higher-priority system rules" in conspiranoic

    comic = personality_prompt("COMICO")
    assert "COMICO" in comic
    assert "humor oscuro" in comic
    assert "humor atrevido" in comic
    assert "categoría protegida" in comic


def test_personality_prompt_rejects_unknown_to_neutral() -> None:
    prompt = personality_prompt("unknown")
    assert "NEUTRO" in prompt


def test_personality_protocol_is_machine_readable_and_explicit() -> None:
    prompt = personality_prompt("COMICO")
    assert "DEEP33 PERSONALITY CONTROL PROTOCOL v2" in prompt
    assert "ACTIVE_PERSONALITY=COMICO" in prompt
    assert "per-turn runtime control" in prompt
    assert "Do not silently fall back to NEUTRO" in prompt
    assert "not as a generic assistant" in prompt


def test_prepare_messages_keeps_current_personality_as_final_system_instruction(monkeypatch) -> None:
    import asyncio
    import backend.main as main

    class FakeMemory:
        enabled = True

        async def context(self, session_id: str) -> dict:
            return {
                "session": {"session_id": session_id, "personality": "NEUTRO", "preferences": {}},
                "messages": [],
                "memories": [],
            }

    monkeypatch.setattr(main, "memory", FakeMemory())
    request = main.ChatRequest(
        messages=[{"role": "user", "content": "test"}],
        personality="COMICO",
    )

    messages, selected = asyncio.run(main.prepare_messages(request, "test-session"))

    assert selected == "COMICO"
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "system"
    assert "Personality preference: NEUTRO." not in messages[0]["content"]
    assert "ACTIVE_PERSONALITY=COMICO" in messages[1]["content"]
    assert messages[1]["content"].rfind("MODE CHECK:") > messages[1]["content"].find("ACTIVE_PERSONALITY=COMICO")


def test_personality_header_and_body_must_agree() -> None:
    from starlette.requests import Request
    from fastapi import HTTPException
    import backend.main as main

    request = main.ChatRequest(
        messages=[{"role": "user", "content": "test"}],
        personality="COMICO",
    )
    http_request = Request({
        "type": "http",
        "method": "POST",
        "path": "/v1/chat/stream",
        "headers": [
            (b"x-deep33-session-id", b"test-session"),
            (b"x-deep33-personality", b"AGRESIVO"),
        ],
    })

    try:
        main.resolve_personality_request(request, http_request)
    except HTTPException as exc:
        assert exc.status_code == 409
        assert exc.detail == "DEEP33_PERSONALITY_TRANSPORT_MISMATCH"
    else:
        raise AssertionError("expected transport mismatch")


def test_chat_endpoint_returns_active_personality_ack(monkeypatch) -> None:
    import backend.main as main
    from fastapi.testclient import TestClient

    async def fake_generate(request, session_id, **kwargs):
        selected = main.normalize_personality(request.personality)
        return {
            "result": {
                "role": "assistant",
                "text": "ok",
                "model": "test",
                "provider": "test",
                "personality": selected,
            }
        }

    monkeypatch.setattr(main, "generate", fake_generate)
    client = TestClient(main.app)

    response = client.post(
        "/v1/chat",
        headers={
            "X-DEEP33-Session-Id": "personality-ack-test",
            "X-DEEP33-Personality": "CONSPIRANOICO",
        },
        json={
            "messages": [{"role": "user", "content": "test"}],
            "personality": "CONSPIRANOICO",
        },
    )

    assert response.status_code == 200
    assert response.headers["X-DEEP33-Personality"] == "CONSPIRANOICO"
    assert response.json()["result"]["personality"] == "CONSPIRANOICO"
