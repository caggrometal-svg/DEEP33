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
        assert "function rssText(itemXml: string, tag: string)" in source
        assert "function hasRecentPublication(value: unknown, maxAgeDays = 7)" in source
        assert 'path.startsWith("/rss/articles/")' in source
        assert "genericLandingPage" in source
        if path.endswith("deep33-proxy/index.ts"):
            assert 'results: finalResults.map(({ provider: _provider, domain: _domain, ...result }) => ({ ...result, provider: _provider }))' in source


def test_secondary_edge_idempotency_is_scoped_to_authenticated_owner() -> None:
    source = read("supabase/functions/deep33-tertiary/index.ts")
    assert "memory_profile_id: context.memoryProfileId" in source
    assert "owner_user_id: context.memoryProfileId" in source
    assert "memoryProfileId: string;" in source
    idempotency_calls = re.findall(
        r"buildEdgeIdempotencyContext\(([\s\S]*?)\n\s*\);",
        source,
    )
    assert len(idempotency_calls) == 4  # Builder declaration plus three calls.
    assert sum(
        bool(re.search(r"\bsessionId,\s+memoryProfileId,\s+idempotencyKey,", call))
        for call in idempotency_calls
    ) == 3


def test_production_e2e_requires_relevant_live_search_results() -> None:
    source = read(".github/workflows/production-e2e.yml")
    assert '.checks.INTERNET=="PASS"' in source
    assert '.checks.DNS=="PASS"' in source
    assert source.count("(.search.verification.relevant_results // 0) >= 1") == 2


def test_secondary_memory_calls_send_internal_token() -> None:
    source = read("supabase/functions/deep33-tertiary/index.ts")
    memory_call = source.split("async function memoryCall(", 1)[1].split(
        "\nasync function hybridCall(", 1
    )[0]
    assert '"x-deep33-internal-token": SUPABASE_SECRET_KEY' in memory_call
    assert 'Authorization: "Bearer " + SUPABASE_SECRET_KEY' in memory_call

def test_production_e2e_retries_transient_real_inference_failures() -> None:
    source = read(".github/workflows/production-e2e.yml")
    assert "for attempt in $(seq 1 4); do" in source
    assert 'DEEP33_REAL_INFERENCE_NOT_READY attempt=$attempt http=$status' in source
    assert 'DEEP33_REAL_INFERENCE_PASS' in source
    assert 'X-Idempotency-Key: prod-${GITHUB_RUN_ID}-${attempt}' in source

def test_ci_recovery_and_release_gates_report_failures_instead_of_skipping() -> None:
    source = read(".github/workflows/ci.yml")
    recovery = source.split("  recovery_gate:", 1)[1].split("  release_gate:", 1)[0]
    release = source.split("  release_gate:", 1)[1]
    assert "needs: [android_e2e, android_validation, change_scope]" in recovery
    assert "always()" in recovery
    assert "needs.android_validation.result == 'success'" in recovery
    assert (
        "needs: [change_scope, backend_unit, production_realtime_search, "
        "android_validation, performance_gate, android_feature_instrumentation, "
        "production_gate, android_e2e, recovery_gate]"
    ) in release
    assert "always() && github.event_name == 'push' && github.ref == 'refs/heads/main'" in release
    assert "DEEP33_RELEASE_BLOCKED" in release
    for gate in (
        "CHANGE_SCOPE", "BACKEND_UNIT", "REALTIME_SEARCH", "ANDROID_VALIDATION",
        "PERFORMANCE_GATE", "ANDROID_FEATURE", "PRODUCTION_GATE",
        "ANDROID_E2E", "RECOVERY_GATE",
    ):
        assert f"DEEP33_{gate}_RESULT" in release
    assert '"success success success success success success success success success"' in release

def test_secondary_edge_has_authenticated_render_ai_fallback() -> None:
    source = read("supabase/functions/deep33-tertiary/index.ts")
    providers = source.split("function edgeProviders()", 1)[1].split(
        "\nfunction edgeAIConfigured()", 1
    )[0]
    assert 'name: "render-backend-fallback"' in providers
    assert 'url: EDGE_AI_UPSTREAM_URL + "/v1/ai/generate"' in providers
    assert 'model: "kilo-auto/small"' in providers
    assert "requires_auth: true" in providers
    assert 'provider.name === "render-backend-fallback"' in source
    assert 'const EDGE_AI_UPSTREAM_URL = "https://deep33-backend.onrender.com"' in source

def test_android_edge_requests_include_publishable_key_and_bearer_auth() -> None:
    source = read("android/app/src/main/java/cl/caggrometal/deep33/Deep33Api.kt")
    assert source.count(
        'connection.setRequestProperty("apikey", BuildConfig.DEEP33_SUPABASE_PUBLISHABLE_KEY)'
    ) == 2
    assert source.count(
        '?.let { connection.setRequestProperty("Authorization", "Bearer " + it) }'
    ) == 2

def test_secondary_edge_render_fallback_supports_streaming() -> None:
    source = read("supabase/functions/deep33-tertiary/index.ts")
    assert "stream_url?: string" in source
    assert 'stream_url: EDGE_AI_UPSTREAM_URL + "/v1/chat/stream"' in source
    assert 'fetch(provider.stream_url || provider.url, {' in source
    assert 'const stream_url = String(value.stream_url || url).trim();' in source
    assert 'if (!isSecureHttpsUrl(url) || !isSecureHttpsUrl(stream_url)' in source

def test_edge_render_fallback_forwards_session_and_idempotency_headers() -> None:
    primary = read("supabase/functions/deep33-proxy/index.ts")
    secondary = read("supabase/functions/deep33-tertiary/index.ts")
    for source in (primary, secondary):
        assert '"X-DEEP33-Session-Id": sessionId' in source
        assert '"X-Idempotency-Key": idempotencyKey' in source
        assert '"X-DEEP33-Skip-Web-Tools": "true"' in source
    assert 'leaseContext?.sessionId || sessionId' in secondary
    assert 'idempotencyContext?.sessionId || ""' in secondary
    assert 'leaseContext?.sessionId || ""' in primary
    assert 'leaseContext?.idempotencyKey || ""' in primary
    assert 'requestId + "-stream-fallback"' in primary

def test_primary_edge_diagnostics_forward_session_to_render_fallback() -> None:
    source = read("supabase/functions/deep33-proxy/index.ts")
    signature = source.split("async function callEdgeAI(", 1)[1].split(
        "): Promise<{ body:", 1
    )[0]
    assert 'sessionId = ""' in signature
    assert "leaseContext?.sessionId || sessionId || \"\"" in source
    assert source.count('req.headers.get("x-deep33-session-id") || ""') >= 2
    assert "sessionId," in source


def test_country_scoped_news_search_rejects_unrelated_google_news_stories() -> None:
    for path in (
        "supabase/functions/deep33-proxy/index.ts",
        "supabase/functions/deep33-tertiary/index.ts",
    ):
        source = read(path)
        assert (
            'if (googleNewsStory) return !countryIntent || '
            r'/\bchile\b|chilean|\blas\s+condes\b|\bsantiago\b/i.test(evidence) '
            '|| host.endsWith(".cl");'
        ) in source


def test_diagnostic_probe_trims_provider_whitespace_and_recovery_retries() -> None:
    for path in (
        "supabase/functions/deep33-proxy/index.ts",
        "supabase/functions/deep33-tertiary/index.ts",
    ):
        source = read(path)
        assert source.count('const ok = text.trim() === "DEEP33_DIAGNOSTIC_OK";') == 2
        assert 'const ok = text === "DEEP33_DIAGNOSTIC_OK";' not in source

    source = read(".github/workflows/ci.yml")
    recovery = source.split("  recovery_gate:", 1)[1].split("  release_gate:", 1)[0]
    assert "for attempt in $(seq 1 3)" in recovery
    assert "RECOVERY_INFERENCE_NOT_READY attempt=$attempt" in recovery
    assert '.online==true and .checks.MODEL=="PASS" and .checks.CHAT=="PASS"' in recovery
    assert 'test "$inference_ok" = "1"' in recovery


def test_production_gate_retries_transient_inference_failures() -> None:
    source = read(".github/workflows/ci.yml")
    production = source.split("  production_gate:", 1)[1].split(
        "  android_e2e:", 1
    )[0]
    assert "PRIMARY_INFERENCE_NOT_READY attempt=$attempt" in production
    assert 'test "$diagnostics_ok" = "1"' in production
    assert '.online==true and .checks.MODEL=="PASS" and .checks.CHAT=="PASS"' in production
