from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
EDGE_FILES = (
    REPO_ROOT / "supabase/functions/deep33-proxy/index.ts",
    REPO_ROOT / "supabase/functions/deep33-tertiary/index.ts",
)


def test_edge_runtime_contains_real_dialogue_policy() -> None:
    required = (
        "function dialoguePolicyInstruction(",
        "No conviertas una respuesta en un informe",
        "No uses tablas, encabezados, secciones",
        "En particular, no uses fórmulas artificiales",
        "En resumen",
        "Cuando la pregunta ya quedó respondida, termina.",
        "No respondas con estructuras del tipo Introducción, Análisis, Estudio, Resumen o Conclusión.",
        "DEEP33 dialoga; no redacta informes.",
        "DEEP33 CONVERSATION CONTROL v2.",
    )

    for path in EDGE_FILES:
        source = path.read_text(encoding="utf-8")
        for fragment in required:
            assert fragment in source, f"{fragment!r} missing from {path}"


def test_edge_runtime_places_conversation_control_in_final_system_message() -> None:
    for path in EDGE_FILES:
        source = path.read_text(encoding="utf-8")
        build_start = source.index("function buildEdgeMessages(")
        build_end = source.index("\nconst UPSTREAM_IDENTITY_BRANDS", build_start)
        build = source[build_start:build_end]

        assert "const conversationControl = dialoguePolicyInstruction(messages);" in build
        assert 'return [\n    ...personalitySystem,' in build
        assert '+ "\\n" + conversationControl' in build


def test_web_final_style_lock_reasserts_conversation_policy() -> None:
    for path in EDGE_FILES:
        source = path.read_text(encoding="utf-8")
        assert (
            'dialoguePolicyInstruction(enrichedMessages) +\n'
            '            " This conversation-control block is authoritative for response shape."'
        ) in source


def test_edge_provider_fallback_urls_are_declared_before_use() -> None:
    for path in EDGE_FILES:
        source = path.read_text(encoding="utf-8")
        assert "const EDGE_AI_UPSTREAM_URLS = [" in source
        assert '"https://deep33-backend.onrender.com"' in source
        assert '"https://deep33-api.onrender.com"' in source
        assert '"https://deep33-backup.onrender.com"' in source
        assert "const EDGE_AI_UPSTREAM_URL = EDGE_AI_UPSTREAM_URLS[0];" in source


def test_realtime_search_does_not_discard_all_undated_current_results() -> None:
    for path in EDGE_FILES:
        source = path.read_text(encoding="utf-8")
        assert "const publicationDateUnknown = !publishedAt || !Number.isFinite(publicationTimestamp);" in source
        assert "const newsFreshEnough = !currentNews || hasRecentPublication(publishedAt, 7) ||" in source
        assert "(googleNewsStory && publicationDateUnknown)" in source
        assert "googleNewsStory || publicationDateUnknown" not in source


def test_current_date_time_search_has_targeted_clock_sources() -> None:
    for path in EDGE_FILES:
        source = path.read_text(encoding="utf-8")
        assert "function edgeCurrentDateTimeQuery(query: string): boolean" in source
        assert "site:timeanddate.com/worldclock/chile/santiago" in source
        assert "site:time.is/Santiago" in source
        assert "if (edgeCurrentDateTimeQuery(query)) {" in source
        assert "timeAndDateSantiago || timeIsSantiago" in source


def test_empty_current_date_time_search_has_live_verified_clock_fallback() -> None:
    for path in EDGE_FILES:
        source = path.read_text(encoding="utf-8")
        assert "async function runCurrentDateTimeSourceFallback(" in source
        assert 'url: "https://www.timeanddate.com/worldclock/chile/santiago"' in source
        assert 'url: "https://time.is/Santiago"' in source
        assert "if (!finalResults.length && edgeCurrentDateTimeQuery(plan.original)) {" in source or \
               "if (!results.length && edgeCurrentDateTimeQuery(plan.original)) {" in source
        assert "if (!response.ok) return null;" in source
        assert "if (!trustedClockPath) return null;" in source


def test_edge_current_news_filter_recognizes_global_reporting_synonyms() -> None:
    for path in EDGE_FILES:
        source = path.read_text(encoding="utf-8")
        assert "|internacional|international|global|globales|mundial|mundiales|mundo|world|worldwide|" in source
        assert "if (!hasNewsEvidence) return false;" in source

def test_edge_fallback_accepts_native_deep33_response_text() -> None:
    for path in EDGE_FILES:
        source = path.read_text(encoding="utf-8")
        assert 'const result = body.result && typeof body.result === "object"' in source
        assert 'if (result && typeof result.text === "string") return result.text;' in source


def test_internal_render_failover_uses_circuit_breaker_when_redundant() -> None:
    for path in EDGE_FILES:
        source = path.read_text(encoding="utf-8")
        assert "const criticalRenderFallback =" in source
        assert "providers.length === 1 && isInternalBackendProvider(provider)" in source
        assert "Date.now() < circuit.openUntil && !criticalRenderFallback" in source


def test_explicit_inference_probe_uses_the_streaming_chat_path() -> None:
    for path in EDGE_FILES:
        source = path.read_text(encoding="utf-8")
        probe = source.split("async function probeInference(", 1)[1].split(
            "async function probeMemory(", 1
        )[0]
        diagnostics = source.split('if (path === "/v1/ai/diagnostics"', 1)[1].split(
            'if (path === "/v1/ai/generate"', 1
        )[0]
        assert "await streamEdgeAI(" in probe
        assert "const text = response.text;" in probe
        assert "await streamEdgeAI(" in diagnostics
        assert "callEdgeAI({" not in diagnostics


def test_readiness_route_does_not_run_model_inference() -> None:
    for path in EDGE_FILES:
        source = path.read_text(encoding="utf-8")
        start = source.index("async function readinessResponse()")
        end = source.index("\\n}\\n\\nDeno.serve".replace("\\n", "\n"), start)
        readiness = source[start:end]
        assert "ROUTINE_READINESS_SKIPS_MODEL_INFERENCE" in readiness
        assert "probeInference(" not in readiness
        assert 'const body = await readinessResponse();' in source
        assert 'path === "/ready" && req.method === "GET"' in source

def test_provider_failover_switches_without_retry_waits() -> None:
    for path in EDGE_FILES:
        source = path.read_text(encoding="utf-8")
        assert "const EDGE_AI_RETRY_COUNT = 0;" in source
        assert "for (const provider of providers)" in source
        assert "addInternalInferenceBackends(providers);" in source
        assert "render-backend-fallback" in source
        assert "render-api-fallback" in source
        assert "render-backup-fallback" in source


def test_realtime_search_uses_bounded_first_query_and_lazy_fallback() -> None:
    function_names = (
        "async function publicWebSearchUncached(",
        "async function publicWebSearch(query: string)",
    )
    for path, function_name in zip(EDGE_FILES, function_names):
        source = path.read_text(encoding="utf-8")
        start = source.index(function_name)
        end = (
            source.index("async function edgeSearch(", start)
            if "publicWebSearch(query: string)" in function_name
            else source.index("function edgeSearchCacheKey(", start)
        )
        search = source[start:end]
        assert ".slice(0, realtimeNewsPlan ? 1 : 3)" in search
        assert "initialQueries.map(async (q) => ({" in search
        assert "for (const retryQuery of plan.queries.slice(1, 3))" in search
        assert "const broadFallbackPromise = realtimeNewsPlan" not in search
        assert "realtimeNewsPlan &&" in search

def test_edge_search_uses_authenticated_bounded_railway_fallback() -> None:
    for path in EDGE_FILES:
        source = path.read_text(encoding="utf-8")
        search = source.split("async function edgeSearch(", 1)[1].split(
            "async function readinessResponse()", 1
        )[0]
        assert 'const railwaySearchUrl = EDGE_AI_UPSTREAM_URL + "/v1/web/search?q="' in search
        assert "await fetch(railwaySearchUrl" in search
        assert '"Authorization": userAuthorization' in search
        assert "AbortSignal.timeout(Math.min(3500, EDGE_INTERNAL_FETCH_TIMEOUT_MS))" in search
        assert 'fallback_path: "railway"' in search
        assert 'logSearchStage("success", railwayOnly ? "railway-recovery" : "railway-fallback", response)' in search
        assert "if (!UPSTREAM" not in search
        assert "fetchUpstream(" not in search



def test_edge_inference_probe_forwards_session_to_internal_backend() -> None:
    for path in EDGE_FILES:
        source = path.read_text(encoding="utf-8")
        stream_start = source.index("async function streamEdgeAI(")
        stream_end = source.index("function normalizePersonality(", stream_start)
        stream = source[stream_start:stream_end]
        probe_start = source.index("async function probeInference(")
        probe_end = source.index("async function probeMemory(", probe_start)
        probe = source[probe_start:probe_end]

        assert 'fallbackSessionId = ""' in stream
        assert 'idempotencyContext?.sessionId || fallbackSessionId || ""' in stream
        assert "async function probeInference(sessionId: string" in probe
        assert "null, userAuthorization, sessionId" in probe


def test_edge_navigation_recovery_uses_new_domains_and_preserves_readable_sources() -> None:
    for path in EDGE_FILES:
        source = path.read_text(encoding="utf-8")
        nav_start = source.index("async function buildNavigatedWebEvidence(")
        nav_end = source.find("\nasync function readJson(", nav_start + 1)
        if nav_end < 0:
            nav_end = source.find("\nconst EDGE_AI_REQUIRES_AUTH", nav_start + 1)
        assert nav_end > nav_start
        navigation = source[nav_start:nav_end]
        assert "excludedUrls: Set<string> = new Set<string>()" in navigation
        assert "excludedDomains: Set<string> = new Set<string>()" in navigation
        assert "attempted_urls: [...attemptedUrls]" in navigation
        assert "attempted_domains: [...attemptedDomains]" in navigation
        assert "scheduledDomains.has(hostname)" in navigation
        assert "excludedDomains.has(hostname)" in navigation

        chat_start = source.index('if (path === "/v1/chat/stream" && req.method === "POST")')
        chat_end = source.index("if (!edgeAIConfigured())", chat_start)
        chat = source[chat_start:chat_end]
        assert 'search.fallback_path !== "railway"' in chat
        assert "buildWebSearchQuery(query)," in chat
        assert "userAuthorization," in chat
        assert "recoverySearch," in chat
        assert "!excludedDomains.has(hostname)" in chat
        assert "const sources = readableSources;" in chat
        assert "if (sources.length < MIN_READABLE_WEB_SOURCES)" in chat
