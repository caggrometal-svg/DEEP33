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


def test_publishable_supabase_key_enables_memory_client(monkeypatch):
    for name in (
        "DEEP33_MEMORY_URL",
        "SUPABASE_SERVICE_ROLE_KEY",
        "SUPABASE_ANON_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SUPABASE_URL", "https://project.supabase.co")
    monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", "sb_publishable_test")

    client = MemoryClient()

    assert client.enabled is True
    assert client.function_url == "https://project.supabase.co/functions/v1/deep33-memory"
    assert client.api_key == "sb_publishable_test"


def test_service_role_key_precedes_publishable_key(monkeypatch):
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service_role_test")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon_test")
    monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", "sb_publishable_test")

    assert MemoryClient._configured_api_key() == "service_role_test"


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

        async def sync(self, session_id, messages, personality=None, preferences=None, memory_profile_id=None, access_token=None):
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
                    headers={"X-DEEP33-Session-Id": "memory-test-session"},
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
            assert body["session_id"].endswith(":memory-test-session")
            assert body["session_id"] != "memory-test-session"
            assert body["saved"] == 2
        finally:
            main.memory = original

    asyncio.run(exercise())


def test_memory_client_uses_user_token_as_bearer_and_keeps_api_key_separate():
    import json

    seen = {}

    def handler(request):
        seen["authorization"] = request.headers.get("authorization")
        seen["apikey"] = request.headers.get("apikey")
        seen["internal_token"] = request.headers.get("x-deep33-internal-token")
        seen["payload"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json={"ok": True, "messages": []})

    async def exercise():
        client = MemoryClient("https://memory.test/function", "anon-api-key", 2)
        client._http_client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            timeout=httpx.Timeout(2),
        )
        try:
            result = await client.context(
                "session",
                memory_profile_id="user-uuid",
                access_token="verified-user-jwt",
            )
            assert result["ok"] is True
        finally:
            await client.close()

    asyncio.run(exercise())
    assert seen["authorization"] == "Bearer verified-user-jwt"
    assert seen["apikey"] == "anon-api-key"
    assert seen["internal_token"] == "anon-api-key"
    # Ownership comes only from the verified JWT subject; a separate client
    # profile id must not override or conflict with the authenticated identity.
    assert "memory_profile_id" not in seen["payload"]


def test_memory_client_defaults_to_configured_api_key_without_user_token():
    seen = {}

    def handler(request):
        seen["authorization"] = request.headers.get("authorization")
        return httpx.Response(200, json={"ok": True})

    async def exercise():
        client = MemoryClient("https://memory.test/function", "anon-api-key", 2)
        client._http_client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            timeout=httpx.Timeout(2),
        )
        try:
            assert (await client.ping())["ok"] is True
        finally:
            await client.close()

    asyncio.run(exercise())
    assert seen["authorization"] == "Bearer anon-api-key"

def test_memory_write_actions_forward_user_token_and_use_jwt_subject():
    import json

    seen = []

    def handler(request):
        seen.append({
            "authorization": request.headers.get("authorization"),
            "payload": json.loads(request.content.decode("utf-8")),
        })
        return httpx.Response(200, json={"ok": True})

    async def exercise():
        client = MemoryClient("https://memory.test/function", "anon-api-key", 2)
        client._http_client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            timeout=httpx.Timeout(2),
        )
        try:
            await client.remember(
                "session",
                "context",
                "saved",
                memory_profile_id="client-profile-id",
                access_token="verified-user-jwt",
            )
            await client.set_preferences(
                "session",
                personality="NEUTRO",
                memory_profile_id="client-profile-id",
                access_token="verified-user-jwt",
            )
        finally:
            await client.close()

    asyncio.run(exercise())
    assert len(seen) == 2
    assert all(item["authorization"] == "Bearer verified-user-jwt" for item in seen)
    assert all("memory_profile_id" not in item["payload"] for item in seen)



def test_streaming_generation_passes_verified_user_token_to_memory_context() -> None:
    from pathlib import Path
    import backend.main as main

    source = Path(main.__file__).read_text(encoding="utf-8")
    stream_section_start = source.index("context_task = asyncio.create_task(\n        prepare_messages(")
    stream_section_end = source.index("raw_messages = [", stream_section_start)
    context_call = source[stream_section_start:stream_section_end]

    assert "memory_profile_id," in context_call
    assert "access_token=access_token," in context_call

def test_authenticated_context_bypasses_process_local_stale_cache():
    import json

    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        assert request.url.path.endswith("/function")
        assert request.headers.get("authorization") == "Bearer verified-user-jwt"
        return httpx.Response(
            200,
            json={
                "ok": True,
                "messages": [
                    {"role": "user", "content": "latest persisted turn"},
                ],
            },
        )

    async def exercise():
        client = MemoryClient("https://memory.test/function", "anon-api-key", 1)
        cache_key = ("user-uuid:session", "user-uuid")
        stale = {
            "ok": True,
            "messages": [
                {"role": "user", "content": "stale cached turn"},
            ],
        }
        client._context_cache[cache_key] = (float("inf"), stale)
        client._http_client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            timeout=httpx.Timeout(1),
        )
        try:
            result = await client.context(
                "user-uuid:session",
                memory_profile_id="user-uuid",
                access_token="verified-user-jwt",
            )
            assert result["messages"][0]["content"] == "latest persisted turn"
            # Authenticated reads neither consume nor refresh another worker's
            # process-local cache entry: they always fetch source-of-truth state.
            assert client._context_cache[cache_key][1] == stale
        finally:
            await client.close()

    asyncio.run(exercise())
    assert calls == 1
\n