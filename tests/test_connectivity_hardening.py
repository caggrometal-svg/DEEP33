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
    assert 'private fun validateEndpoint(' in source
    assert 'scheme != "https"' in source
    assert 'uri.userInfo != null' in source
    assert 'uri.query != null' in source
    assert 'uri.fragment != null' in source
    assert 'allowIsolatedTestEndpoint' in source
    assert 'BuildConfig.DEBUG' in source
    assert 'host.endsWith(".invalid")' in source
    assert 'responseContentType.contains("json", ignoreCase = true)' in source


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
    assert 'Transport contract is immutable by build environment' in source
    assert 'rejected overrides' in source
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
    assert 'from urllib.parse import urlsplit' in gateway
    assert 'parsed.username is not None' in gateway
    assert 'from urllib.parse import urlsplit' in memory
    assert 'parsed.password is not None' in memory
    assert 'NETWORK_CHECK_URL = os.getenv("NETWORK_CHECK_URL", "https://www.google.com/generate_204")' in main


def test_edge_upstream_is_https_only_and_redirect_strict() -> None:
    for path in (
        "supabase/functions/deep33-proxy/index.ts",
        "supabase/functions/deep33-tertiary/index.ts",
    ):
        source = read(path)
        assert 'const UPSTREAM = isSecureHttpsUrl(RAW_UPSTREAM)' in source
        assert 'url.port === "" || url.port === "443"' in source
        assert 'redirect: "error"' in source
        assert 'AbortSignal.timeout(EDGE_INTERNAL_FETCH_TIMEOUT_MS)' in source
        assert 'function isSecureHttpsUrl' in source
        assert '!url.username' in source
        assert '!url.password' in source
        assert '!url.search' in source
        assert '!url.hash' in source
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

    production_e2e = read(".github/workflows/production-e2e.yml")
    assert "https://opocgzydeknuchtrqzfa.supabase.co/functions/v1/deep33-proxy" in production_e2e
    assert "http://" not in production_e2e
    assert "c-33.blitz.cloud" not in production_e2e.lower()


def test_release_gate_dispatches_certified_apk_workflows() -> None:
    source = read(".github/workflows/ci.yml")
    assert "actions: write" in source
    assert "gh workflow run android-debug-apk.yml --ref production-pass" in source
    assert "gh workflow run android-fast-apk.yml --ref production-pass" in source
    assert "gh workflow run android-package.yml --ref production-pass" in source


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



def test_edge_connectivity_audit_scopes_internal_memory_probe_to_authenticated_user() -> None:
    for path in (
        "supabase/functions/deep33-proxy/index.ts",
        "supabase/functions/deep33-tertiary/index.ts",
    ):
        source = read(path)
        assert "async function probeMemory(sessionId: string, memoryProfileId?: string)" in source
        assert 'memoryCall("context", sessionId, ownerScope)' in source
        assert "probeMemory(auditSession, memoryProfileId)" in source


def test_edge_realtime_search_does_not_require_generic_geography_words_in_every_result() -> None:
    source = read("supabase/functions/deep33-proxy/index.ts")
    assert "function edgeFilterSearchResults(" in source
    assert '"comuna","comunas","municipio","municipios","municipalidad","municipalidades"' in source
    assert 'tokens.delete("chile")' in source
    assert '"noticias recientes en Chile"' in source


def test_edge_connectivity_audit_reports_internet_dns_and_search_separately() -> None:
    source = read("supabase/functions/deep33-proxy/index.ts")
    assert "async function probeInternetConnectivity()" in source
    assert "async function auditSearch(sessionId: string)" in source
    assert 'INTERNET: internetOk ? "PASS" : "FAIL"' in source
    assert 'DNS: dnsOk ? "PASS" : "FAIL"' in source
    assert 'SEARCH: searchOk ? "PASS" : "FAIL"' in source
    assert "audit_attempts" in source


def test_edge_ai_render_fallback_receives_the_authenticated_bearer() -> None:
    source = read("supabase/functions/deep33-proxy/index.ts")
    assert 'idempotencyContext,\n            req.headers.get("authorization") || "",\n          );' in source
    assert 'provider.requires_auth && authorization ? { "Authorization": authorization }' in source
    assert 'provider.name === "render-backend-fallback"' in source
    assert 'provider.url.startsWith(EDGE_AI_UPSTREAM_URL + "/")' in source
    assert 'const authorization = renderBackendFallback && userAuthorization.startsWith("Bearer ")' in source


def test_realtime_edge_search_filters_static_wikipedia_and_uses_actual_providers() -> None:
    for path in (
        "supabase/functions/deep33-proxy/index.ts",
        "supabase/functions/deep33-tertiary/index.ts",
    ):
        source = read(path)
        assert 'host.endsWith(".wikipedia.org")' in source
        assert "edgeFilterSearchResults" in source
        assert "edgeProvidersForResults" in source
        assert "provider: item.value.name" in source
        if path.endswith("deep33-proxy/index.ts"):
            assert 'results: finalResults.map(({ provider: _provider, domain: _domain, ...result }) => ({ ...result, provider: _provider }))' in source
