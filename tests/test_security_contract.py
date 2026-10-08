from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ANDROID = ROOT / "android/app/src/main/java/cl/caggrometal/deep33"
PROXY = ROOT / "supabase/functions/deep33-proxy/index.ts"
TERTIARY = ROOT / "supabase/functions/deep33-tertiary/index.ts"
MEMORY = ROOT / "supabase/functions/deep33-memory/index.ts"
CONFIG = (ROOT / "supabase/config.toml").read_text(encoding="utf-8")


def test_edge_entrypoints_require_jwt_in_repo_config():
    assert "[functions.deep33-proxy]" in CONFIG
    assert "[functions.deep33-tertiary]" in CONFIG
    assert "[functions.deep33-memory]" in CONFIG
    assert 'verify_jwt = true' in CONFIG


def test_proxy_enforces_authenticated_owner_and_rate_limit():
    source = PROXY.read_text(encoding="utf-8")
    assert "requireAuthenticatedUser(req)" in source
    assert "scopedSession(ownerUserId, clientSessionId)" in source
    assert "deep33_rate_limit_claim" in source
    assert 'throw new Deep33HttpError(429, "DEEP33_RATE_LIMITED")' in source


def test_memory_rejects_client_profile_spoofing():
    source = MEMORY.read_text(encoding="utf-8")
    assert "resolveOwnerId(req, body)" in source
    assert "claimed !== userId" in source
    assert "MEMORY_AUTH_REQUIRED" in source


def test_tertiary_keeps_authorization_header_through_failover():
    source = TERTIARY.read_text(encoding="utf-8")
    assert '"authorization"' in source
    assert 'headers.delete("Authorization")' not in source


def test_android_does_not_ship_provider_secret_or_client_profile_authority():
    api = (ANDROID / "Deep33Api.kt").read_text(encoding="utf-8")
    build = (ROOT / "android/app/build.gradle.kts").read_text(encoding="utf-8")
    auth = (ANDROID / "SupabaseAuthManager.kt").read_text(encoding="utf-8")
    assert '"X-DEEP33-Memory-Profile-Id"' not in api
    assert "AndroidKeyStore" in auth
    assert "DEEP33_SUPABASE_PUBLISHABLE_KEY" in build
    assert "SUPABASE_SERVICE_ROLE_KEY" not in build

def test_android_auth_uses_deep33_bootstrap_and_not_direct_signup():
    source = (ANDROID / "SupabaseAuthManager.kt").read_text(encoding="utf-8")
    assert "/functions/v1/deep33-auth" in source
    assert '"/signup"' not in source
    assert '"/token?grant_type=refresh_token"' in source
def test_deep33_auth_bootstrap_contract():
    source = (ROOT / "supabase/functions/deep33-auth/index.ts").read_text(encoding="utf-8")
    config = CONFIG
    assert "SUPABASE_SERVICE_ROLE_KEY" in source
    assert 'deep33_auth_profile_lookup' in source
    assert 'deep33_auth_profile_bind' in source
    assert 'memory_profile_id' in source
    assert "[functions.deep33-auth]" in config
    assert "verify_jwt = false" in config
