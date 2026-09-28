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
    assert "DIRECTO" in system
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
