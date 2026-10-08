from __future__ import annotations

import asyncio

import httpx

from backend.memory import MemoryClient, extract_context_system_message, merge_messages


def test_merge_messages_deduplicates_remote_and_request():
    remote = [
        {"role": "user", "content": "uno"},
        {"role": "assistant", "content": "dos"},
    ]
    request = [
        {"role": "assistant", "content": "dos"},
        {"role": "user", "content": "tres"},
    ]
    assert merge_messages(remote, request) == [
        {"role": "user", "content": "uno"},
        {"role": "assistant", "content": "dos"},
        {"role": "user", "content": "tres"},
    ]


def test_disabled_memory_is_noop():
    client = MemoryClient(function_url="", api_key="")
    assert client.enabled is False


def test_memory_client_success():
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={"ok": True, "messages": [{"role": "user", "content": "persisted"}]},
        )
    )
    original = httpx.AsyncClient

    class Client(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    httpx.AsyncClient = Client
    try:
        client = MemoryClient("https://memory.test/function", "anon", 2)
        result = asyncio.run(client.context("session"))
        assert result["ok"] is True
    finally:
        httpx.AsyncClient = original



def test_context_system_message_contains_preferences_and_memory():
    context = {
        "session": {
            "personality": "DIRECTO",
            "preferences": {"language": "es"},
        },
        "memories": [
            {"kind": "explicit", "content": "El proyecto se llama DEEP33."},
        ],
    }
    system = extract_context_system_message(context)
    assert system is not None
    assert "language" in system
    assert "DIRECTO" not in system
    assert "DEEP33" in system
    assert "Do not reveal" in system



def test_extract_context_messages_filters_internal_context():
    from backend.memory import extract_context_messages

    data = {
        "messages": [
            {"role": "system", "content": "DEEP33 internal context. secret"},
            {"role": "user", "content": "visible"},
        ]
    }
    assert extract_context_messages(data) == [{"role": "user", "content": "visible"}]



def test_extract_context_messages_rejects_all_remote_system_messages():
    from backend.memory import extract_context_messages

    data = {
        "messages": [
            {"role": "system", "content": "malicious instruction"},
            {"role": "assistant", "content": "safe"},
            {"role": "user", "content": "question"},
        ]
    }
    assert extract_context_messages(data) == [
        {"role": "assistant", "content": "safe"},
        {"role": "user", "content": "question"},
    ]


def test_memory_request_hash_is_deterministic():
    payload = {"messages": [{"role": "user", "content": "hola"}], "personality": "NEUTRO"}
    first = MemoryClient._request_hash("sync", "session", payload)
    second = MemoryClient._request_hash("sync", "session", dict(payload))
    assert first == second
    assert len(first) == 64

def test_memory_edge_sync_persists_session_before_messages():
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1]
        / "supabase"
        / "functions"
        / "deep33-memory"
        / "index.ts"
    ).read_text(encoding="utf-8")

    session_upsert = source.index(
        '.from("deep33_sessions")'
    )
    session_patch = source.index("upsert(sessionPatch, { onConflict: \"session_id\" })")
    incoming = source.index(
        "const incoming = Array.isArray(body.messages) ? body.messages : [];"
    )

    assert session_upsert < incoming
    assert session_patch < incoming



def test_memory_sync_endpoint_bridges_android_to_memory_client():
    from backend import main

    class StubMemory:
        enabled = True

        async def sync(self, session_id, messages, personality=None, preferences=None, memory_profile_id=None, owner_user_id=None):
            return {
                "ok": True,
                "session_id": session_id,
                "saved": len(messages),
                "personality": personality,
                "preferences": preferences,
            }

    async def exercise():
        original = main.memory
        main.memory = StubMemory()
        try:
            transport = httpx.ASGITransport(app=main.app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://testserver",
            ) as client:
                response = await client.post(
                    "/v1/memory/sync",
                    headers={"Authorization": "Bearer test-client-token", "X-DEEP33-Session-Id": "memory-test-session"},
                    json={
                        "messages": [
                            {"role": "user", "content": "hola"},
                            {"role": "assistant", "content": "respuesta"},
                        ],
                        "personality": "NEUTRO",
                    },
                )
            assert response.status_code == 200
            body = response.json()
            assert body["ok"] is True
            assert body["session_id"] == "memory-test-session"
            assert body["saved"] == 2
        finally:
            main.memory = original

    asyncio.run(exercise())
