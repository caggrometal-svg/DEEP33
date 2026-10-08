from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
ANDROID_ROOT = REPO_ROOT / "android/app/src/main/java/cl/caggrometal/deep33"
EDGE_FILES = (
    REPO_ROOT / "supabase/functions/deep33-proxy/index.ts",
    REPO_ROOT / "supabase/functions/deep33-tertiary/index.ts",
)
MEMORY_FILE = REPO_ROOT / "supabase/functions/deep33-memory/index.ts"


def test_android_uses_a_stable_opaque_profile_identity() -> None:
    store = (ANDROID_ROOT / "SessionStore.kt").read_text(encoding="utf-8")
    identity = (ANDROID_ROOT / "MultiUserIdentity.kt").read_text(encoding="utf-8")

    assert 'profilePrefsName(selectedProfileId)' in store
    assert 'KEY_MEMORY_PROFILE_ID' in store
    assert 'MultiUserIdentity.newProfileId()' in store
    assert 'MultiUserIdentity.newSessionId()' in store
    assert 'PROFILE_PREFIX = "profile-"' in identity


def test_android_sends_profile_scope_on_memory_requests() -> None:
    api = (ANDROID_ROOT / "Deep33Api.kt").read_text(encoding="utf-8")

    assert '"X-DEEP33-Memory-Profile-Id"' in api
    assert "memoryProfileId: String?" in api
    assert "memoryProfileId = memoryProfileId" in api


def test_edge_gateways_propagate_profile_scope_to_memory() -> None:
    for path in EDGE_FILES:
        source = path.read_text(encoding="utf-8")

        assert '"x-deep33-memory-profile-id"' in source
        assert "memoryProfileId" in source
        assert "memoryProfileId?: string" in source
        assert "memory_profile_id: memoryProfileId" not in source or "profileScope" in source
        assert "profileScope" in source
        assert 'memoryCall("context", sessionId, profileScope, ownerUserId)' in source
        assert 'memoryCall("sync", sessionId, {' in source
        assert "...profileScope" in source


def test_memory_function_scopes_remote_data_to_profile() -> None:
    source = MEMORY_FILE.read_text(encoding="utf-8")

    assert 'body.memory_profile_id' in source
    assert "scopeSession(memoryProfileId, clientSessionId)" in source
    assert '.in("session_id", messageSessionIds)' in source
    assert 'row.session_id: profileSessionId' not in source
    assert 'session_id: sessionId' in source
    assert 'memoryProfileId' in source
