from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ANDROID_ROOT = REPO_ROOT / "android/app/src/main/java/cl/caggrometal/deep33"
EDGE_FILES = (
    REPO_ROOT / "supabase/functions/deep33-proxy/index.ts",
    REPO_ROOT / "supabase/functions/deep33-tertiary/index.ts",
)
MEMORY_FILE = REPO_ROOT / "supabase/functions/deep33-memory/index.ts"


def test_android_uses_explicit_per_profile_isolation() -> None:
    store = (ANDROID_ROOT / "SessionStore.kt").read_text(encoding="utf-8")
    identity = (ANDROID_ROOT / "MultiUserIdentity.kt").read_text(encoding="utf-8")

    assert 'name ?: PREFS_NAME + "_" + profileId' in store
    assert 'profileIdOverride: String? = null' in store
    assert 'PROFILE_PREFIX = "profile-"' in identity
    assert "createProfile(" in identity
    assert "switchProfile(" in identity


def test_android_auth_scopes_remote_transport_without_client_authority_header() -> None:
    api = (ANDROID_ROOT / "Deep33Api.kt").read_text(encoding="utf-8")
    auth = (ANDROID_ROOT / "SupabaseAuthManager.kt").read_text(encoding="utf-8")

    assert 'setRequestProperty("Authorization", "Bearer " + it)' in api
    assert "class SupabaseAuthManager(" in auth
    assert "AndroidKeyStore" in auth
    assert '"X-DEEP33-Memory-Profile-Id"' not in api


def test_edge_gateways_derive_owner_from_jwt() -> None:
    for path in EDGE_FILES:
        source = path.read_text(encoding="utf-8")

        assert "Authorization" in source
        assert "supabaseAdmin.auth.getUser" in source or "resolveOwnerId" in source
        assert "scopedSession(" in source


def test_memory_function_rejects_profile_spoofing() -> None:
    source = MEMORY_FILE.read_text(encoding="utf-8")

    assert "resolveOwnerId(" in source
    assert "MEMORY_AUTH_REQUIRED" in source
    assert "claimed !== userId" in source
    assert "scopeSession(" in source
    assert 'const messageSessionIds = [sessionId];' in source
