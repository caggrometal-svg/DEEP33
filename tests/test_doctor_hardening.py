from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_multiuser_storage_is_profile_scoped_and_generation_carries_profile():
    store = read("android/app/src/main/java/cl/caggrometal/deep33/SessionStore.kt")
    service = read("android/app/src/main/java/cl/caggrometal/deep33/Deep33GenerationService.kt")
    activity = read("android/app/src/main/java/cl/caggrometal/deep33/MainActivity.kt")

    assert 'PROFILE_REGISTRY_PREFS = "deep33_user_profiles"' in store
    assert 'profilePrefsName(selectedProfileId)' in store
    assert 'memoryProfileId: String' in store
    assert 'val memoryProfileId: String' in store
    assert 'EXTRA_PROFILE_ID' in service
    assert 'pending.memoryProfileId' in service
    assert 'Deep33GenerationService.start(this, requestId, store.profileId)' in activity
    assert 'Deep33GenerationService.cancel(this, requestId, store.profileId)' in activity


def test_gps_is_temporal_and_not_written_as_exact_coordinates():
    location = read("android/app/src/main/java/cl/caggrometal/deep33/Deep33LocationProvider.kt")
    activity = read("android/app/src/main/java/cl/caggrometal/deep33/MainActivity.kt")
    weather = read("backend/weather.py")

    assert "LOCATION_DEADLINE_MS = 3_500L" in location
    assert "MAX_LOCATION_AGE_MS = 120_000L" in location
    assert "minByOrNull(::score)" in location
    assert "shareCoordinatesWithInference" in location
    assert '"GPS_LOCATION_READY label=' in activity
    assert "latitude" not in activity[activity.find("GPS_LOCATION_READY"):activity.find("GPS_LOCATION_READY") + 120]
    assert 'if key not in {"latitude", "longitude"}' in weather


def test_authenticated_memory_namespace_is_bound_to_user():
    auth = read("supabase/functions/deep33-auth/index.ts")
    proxy = read("supabase/functions/deep33-proxy/index.ts")
    memory = read("supabase/functions/deep33-memory/index.ts")
    backend = read("backend/main.py")

    assert "signInAnonymously" in auth
    assert "AUTH_PROFILE_ALREADY_BOUND" in auth
    assert 'eq("memory_profile_id", localProfileId)' in auth
    assert "resolveAuthenticatedMemoryProfile" in proxy
    assert 'Authorization", "Bearer " + accessToken' in read(
        "android/app/src/main/java/cl/caggrometal/deep33/Deep33Api.kt"
    )
    assert "resolveMemoryProfileId" in memory
    assert 'scopeSession(memoryProfileId, clientSessionId)' in memory
    assert "x-deep33-internal-token" in memory
    assert "authenticated_user_id" in backend
    assert "client_key(identity, session_id)" in backend


def test_backend_context_compaction_is_dynamic():
    source = read("backend/main.py")
    assert "adaptive_context_budget" in source
    assert "compact_context_messages" in source
    assert "DEEP33_CONTEXT_MAX_CHARS" in source
    assert "len(remote) + len(requested)" in source
    assert "CONTINUIDAD COMPACTADA" in source
    assert "requested[-max_messages:]" not in source


def test_sse_accepts_clean_close_and_modern_content():
    source = read("backend/main.py")
    api = read("android/app/src/main/java/cl/caggrometal/deep33/Deep33Api.kt")

    assert "_normalize_stream_content_value" in source
    assert "A clean provider close is valid" in source
    assert "sawStreamData" in api
    assert "message?.let" in api
    assert "extractContentValue" in api


def test_search_has_hard_outer_deadline_and_parallel_diagnostics():
    search = read("tools/web_search.py")
    main = read("backend/main.py")
    edge = read("supabase/functions/deep33-proxy/index.ts")

    assert "asyncio.wait_for(task, timeout=timeout_seconds + 0.75)" in search
    assert "network, gateway_status, inference_result = await asyncio.gather(" in main
    assert "const [health, inference, webStatus] = await Promise.all([" in edge


def test_feedback_is_real_persistent_and_non_adaptive():
    api = read("android/app/src/main/java/cl/caggrometal/deep33/Deep33Api.kt")
    activity = read("android/app/src/main/java/cl/caggrometal/deep33/MainActivity.kt")
    edge = read("supabase/functions/deep33-proxy/index.ts")
    migration = read("supabase/migrations/20261008_deep33_feedback_unique.sql")

    assert "submitFeedback" in api
    assert '"/v1/feedback"' in api
    assert "submitResponseFeedback" in activity
    assert "upsert" in edge
    assert "deep33_feedback" in edge
    assert "onConflict: \"user_id,session_id,response_hash\"" in edge
    assert "deep33_feedback_user_session_response_key" in migration
    assert "model" not in edge[edge.find('path === "/v1/feedback"'):edge.find('path === "/v1/feedback"') + 1600]


def test_release_hardening_and_transport_invariants_remain():
    gradle = read("android/app/build.gradle.kts")
    api = read("android/app/src/main/java/cl/caggrometal/deep33/Deep33Api.kt")
    rules = read("android/app/proguard-rules.pro")

    assert "isMinifyEnabled = true" in gradle
    assert "isShrinkResources = true" in gradle
    assert 'proguard-android-optimize.txt' in gradle
    assert "DEEP33_AUTH_URL" in gradle
    assert "SUPABASE_SERVICE_ROLE_KEY" not in api
    assert "vireonix" not in api.lower()
    assert "No broad keep rules" not in rules


def test_generation_deadline_and_async_checkpoint_contracts_remain():
    service = read("android/app/src/main/java/cl/caggrometal/deep33/Deep33GenerationService.kt")
    api = read("android/app/src/main/java/cl/caggrometal/deep33/Deep33Api.kt")

    assert "GENERATION_DEADLINE_MS = 120_000L" in service
    assert "timeoutMs = remainingMs" in service
    assert "Thread.sleep(retryDelayMs)" in service
    assert "durable = false" in service
    assert "partialOutput = checkpoint.takeLast(CHECKPOINT_TAIL_CHARS)" in service
    assert "MEMORY_SYNC_TIMEOUT_MS" in api
