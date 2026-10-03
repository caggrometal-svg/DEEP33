from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_android_transport_is_https_and_deadline_bounded() -> None:
    source = read("android/app/src/main/java/cl/caggrometal/deep33/Deep33Api.kt")
    assert 'connection.instanceFollowRedirects = false' in source
    assert 'connection.setRequestProperty("Accept-Encoding", "identity")' in source
    assert 'connection.setRequestProperty("Cache-Control", "no-cache")' in source
    assert '.coerceAtLeast(3_000L)' not in source
    assert 'if (remainingMs <= 250L) break' in source
    assert 'value.lowercase().startsWith("https://")' in source


def test_android_manifest_forbids_cleartext_and_keeps_recovery_service_alive() -> None:
    source = read("android/app/src/main/AndroidManifest.xml")
    assert '<uses-permission android:name="android.permission.INTERNET" />' in source
    assert '<uses-permission android:name="android.permission.ACCESS_NETWORK_STATE" />' in source
    assert '<uses-permission android:name="android.permission.WAKE_LOCK" />' in source
    assert 'android:usesCleartextTraffic="false"' in source
    assert 'android:stopWithTask="false"' in source
    assert 'android:exported="false"' in source


def test_android_endpoint_contract_is_immutable_and_distinct() -> None:
    source = read("android/app/build.gradle.kts")
    assert '"https://deep33-backend.onrender.com"' in source
    assert '"https://opocgzydeknuchtrqzfa.supabase.co/functions/v1/deep33-proxy"' in source
    assert '?: ""' in source
    assert 'http://' not in source


def test_backend_provider_and_memory_clients_are_redirect_strict() -> None:
    gateway = read("backend/gateway.py")
    memory = read("backend/memory.py")
    main = read("backend/main.py")
    assert 'follow_redirects=False' in gateway
    assert 'follow_redirects=False' in memory
    assert 'follow_redirects=False' in main
    assert '_provider_url_is_secure' in gateway
    assert 'startswith("https://")' in memory
    assert 'NETWORK_CHECK_URL = os.getenv("NETWORK_CHECK_URL", "https://www.google.com/generate_204")' in main


def test_edge_upstream_is_https_only_and_redirect_strict() -> None:
    for path in (
        "supabase/functions/deep33-proxy/index.ts",
        "supabase/functions/deep33-tertiary/index.ts",
    ):
        source = read(path)
        assert r'const UPSTREAM = /^https:\/\/[^\\s/]+(?:\/.*)?$/i.test(RAW_UPSTREAM)' in source
        assert 'redirect: "error"' in source
        assert 'AbortSignal.timeout(EDGE_INTERNAL_FETCH_TIMEOUT_MS)' in source
        assert 'function isSecureHttpsUrl' in source
        assert 'isSecureHttpsUrl(EDGE_AI_URL)' in source
        assert '!isSecureHttpsUrl(url)' in source
        assert '!isSecureHttpsUrl(MEMORY_FUNCTION_URL)' in source
        assert '!isSecureHttpsUrl(HYBRID_FUNCTION_URL)' in source
        assert r'render\.com|railway\.app|iac33|guqevsjbjyapqjjtutza' in source


def test_transport_workflows_keep_canonical_endpoint_set() -> None:
    expected = {
        "https://deep33-backend.onrender.com",
        "https://opocgzydeknuchtrqzfa.supabase.co/functions/v1/deep33-proxy",
        "https://opocgzydeknuchtrqzfa.supabase.co/functions/v1/deep33-tertiary",
    }
    full_set_paths = [
        ".github/workflows/android-debug-apk.yml",
        ".github/workflows/android-fast-apk.yml",
        ".github/workflows/ci.yml",
    ]
    for path in full_set_paths:
        source = read(path)
        urls = set(re.findall(r'https://[^\s$"]+', source))
        assert expected.issubset(urls), path
        assert "http://" not in source
        assert "c-33.blitz.cloud" not in source.lower()
        assert "iac33" not in source.lower()

    production_e2e = read(".github/workflows/production-e2e.yml")
    assert "https://opocgzydeknuchtrqzfa.supabase.co/functions/v1/deep33-proxy" in production_e2e
    assert "http://" not in production_e2e
    assert "c-33.blitz.cloud" not in production_e2e.lower()
    assert "iac33" not in production_e2e.lower()


def test_apk_workflows_require_certified_production_pass() -> None:
    for path in (
        ".github/workflows/android-fast-apk.yml",
        ".github/workflows/android-debug-apk.yml",
    ):
        source = read(path)
        assert "workflow_dispatch:" in source
        assert 'push:\n    tags: [production-pass]' in source
        assert "push:\n    branches: [main]" not in source
        assert "Require certified production-pass" in source
        assert "APK_CERTIFICATION_GATE_PASS" in source
        assert "DEEP33_PRIMARY_URL" in source

    package = read(".github/workflows/android-package.yml")
    assert '- "production-pass"' in package
