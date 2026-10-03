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
    assert '"https://opocgzydeknuchtrqzfa.supabase.co/functions/v1/deep33-tertiary"' in source
    assert 'canonicalEndpoints.distinct().size == canonicalEndpoints.size' in source
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
        assert 'const UPSTREAM = /^https:\/\/[^\\s/]+(?:\/.*)?$/i.test(RAW_UPSTREAM)' in source
        assert 'redirect: "error"' in source
        assert 'render\\.com|railway\\.app|iac33|guqevsjbjyapqjjtutza' in source


def test_transport_workflows_keep_canonical_endpoint_set() -> None:
    paths = [
        ".github/workflows/android-debug-apk.yml",
        ".github/workflows/android-fast-apk.yml",
        ".github/workflows/ci.yml",
        ".github/workflows/production-e2e.yml",
    ]
    expected = {
        "https://deep33-backend.onrender.com",
        "https://opocgzydeknuchtrqzfa.supabase.co/functions/v1/deep33-proxy",
        "https://opocgzydeknuchtrqzfa.supabase.co/functions/v1/deep33-tertiary",
    }
    for path in paths:
        source = read(path)
        urls = set(re.findall(r'https://[^\s$"]+', source))
        assert expected.issubset(urls), path
        assert "http://" not in source
        assert "c-33.blitz.cloud" not in source.lower()
        assert "iac33" not in source.lower()


def test_fast_apk_workflow_does_not_bypass_repository_ci_contract() -> None:
    source = read(".github/workflows/android-fast-apk.yml")
    assert "gradle :app:assembleDebug --no-daemon" in source
    assert "DEEP33_PRIMARY_URL" in source
    assert "DEEP33_SECONDARY_URL" in source
