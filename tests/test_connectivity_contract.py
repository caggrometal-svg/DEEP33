from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

PRIMARY = "https://opocgzydeknuchtrqzfa.supabase.co/functions/v1/deep33-proxy"
SECONDARY = "https://opocgzydeknuchtrqzfa.supabase.co/functions/v1/deep33-tertiary"
TERTIARY = "https://deep33-backend.onrender.com"
ENDPOINTS = (PRIMARY, SECONDARY, TERTIARY)


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _env_line(text: str, name: str) -> str:
    prefix = f"{name}:"
    for line in text.splitlines():
        if line.strip().startswith(prefix):
            return line.split(":", 1)[1].strip().strip('"')
    raise AssertionError(f"{name} not found")


def test_canonical_endpoints_are_https_and_distinct() -> None:
    assert len(set(ENDPOINTS)) == 3
    assert all(url.startswith("https://") for url in ENDPOINTS)
    assert all("@" not in url and "?" not in url and "#" not in url for url in ENDPOINTS)


def test_android_build_has_three_canonical_transport_routes() -> None:
    text = _read("android/app/build.gradle.kts")
    assert '?: "https://opocgzydeknuchtrqzfa.supabase.co/functions/v1/deep33-proxy"' in text
    assert '?: "https://opocgzydeknuchtrqzfa.supabase.co/functions/v1/deep33-tertiary"' in text
    assert '?: "https://deep33-backend.onrender.com"' in text
    assert 'canonicalEndpoints.distinct().size == canonicalEndpoints.size' in text
    assert 'canonicalEndpoints.all { it.startsWith("https://") && !it.endsWith("/") }' in text


def test_transport_source_rejects_unsafe_endpoints_and_wrong_response_types() -> None:
    text = _read("android/app/src/main/java/cl/caggrometal/deep33/Deep33Api.kt")
    assert "private fun validateEndpoint(raw: String): String?" in text
    assert 'scheme != "https"' in text
    assert "uri.userInfo != null" in text
    assert "uri.query != null" in text
    assert "uri.fragment != null" in text
    assert "instanceFollowRedirects = false" in text
    assert 'connection.contentType.orEmpty().contains("text/event-stream"' in text
    assert 'responseContentType.contains("json", ignoreCase = true)' in text
    assert "activeStreamConnections[requestId]" in text


def test_all_build_workflows_use_the_same_canonical_three_route_order() -> None:
    for path in (
        ".github/workflows/ci.yml",
        ".github/workflows/android-fast-apk.yml",
        ".github/workflows/android-debug-apk.yml",
    ):
        text = _read(path)
        assert _env_line(text, "DEEP33_PRIMARY_URL") == PRIMARY
        assert _env_line(text, "DEEP33_SECONDARY_URL") == SECONDARY
        assert _env_line(text, "DEEP33_TERTIARY_URL") == TERTIARY


def test_package_and_production_checks_preserve_redundancy() -> None:
    package = _read(".github/workflows/android-package.yml")
    assert _env_line(package, "DEEP33_PRIMARY_URL") == PRIMARY
    assert _env_line(package, "DEEP33_SECONDARY_URL") == SECONDARY
    assert _env_line(package, "DEEP33_TERTIARY_URL") == TERTIARY
    assert 'DEEP33_REQUIRE_REDUNDANCY: "true"' in package
    assert "FAILOVER_DIVERSITY_PASS" in package

    e2e = _read(".github/workflows/production-e2e.yml")
    assert _env_line(e2e, "PRIMARY_URL") == PRIMARY


def test_real_android_e2e_checks_runner_and_three_routes() -> None:
    text = _read("android/deep33-real-connectivity-e2e.sh")
    assert "DEEP33_PRIMARY_URL" in text and "DEEP33_SECONDARY_URL" in text and "DEEP33_TERTIARY_URL" in text
    assert "urls=" in text
    assert 'echo "ANDROID_INSTRUMENTATION_PASS tests_verified"' in text
    assert 'grep -Eq "OK \\([0-9]+ tests\\)"' in text


def test_web_fetch_keeps_ssrf_and_redirect_controls() -> None:
    text = _read("tools/web_fetch.py")
    assert "def validate_public_url(url):" in text
    assert "SSRFBlockedError" in text
    assert "ipaddress.ip_address(value).is_global" in text
    assert "follow_redirects=False" in text
    assert "WEB_FETCH_CONTENT_TYPE_BLOCKED" in text


def test_ci_routes_transport_changes_through_production_recovery_gate() -> None:
    text = _read(".github/workflows/ci.yml")
    assert "android/app/build.gradle.kts" in text
    assert "android/app/src/main/java/cl/caggrometal/deep33/Deep33Api.kt" in text
    assert "android/deep33-real-connectivity-e2e.sh" in text
    assert "name: Connectivity hardening contract" in text
    assert "name: Publish only post-gate APK" in text
    assert "needs: recovery_gate" in text