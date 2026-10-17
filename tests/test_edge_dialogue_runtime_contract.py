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
    for path in EDGE_FILES:
        source = path.read_text(encoding="utf-8")
        uncached = "async function publicWebSearchUncached("
        legacy = "async function publicWebSearch(query: string)"
        function_name = uncached if uncached in source else legacy
        start = source.index(function_name)
        end = (
            source.index("function edgeSearchCacheKey(", start)
            if function_name == uncached
            else source.index("async function edgeSearch(", start)
        )
        search = source[start:end]
        assert ".slice(0, realtimeNewsPlan ? 1 : 3)" in search
        assert "initialQueries.map(async (q) => ({" in search
        assert "const recoveryPlan = [" in search
        assert "const recoveryBatches = await Promise.all(recoveryPlan.map(async (candidate) => ({" in search
        assert 'event: "edge_web_search_recovery_complete"' in search
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
        assert "scheduledDomains.has(effectiveDomain)" in navigation
        assert "excludedDomains.has(effectiveDomain)" in navigation

        chat_start = source.index('if (path === "/v1/chat/stream" && req.method === "POST")')
        chat_end = source.index("if (!edgeAIConfigured())", chat_start)
        chat = source[chat_start:chat_end]
        assert 'search.fallback_path !== "railway"' in chat
        assert "buildWebSearchQuery(query)," in chat
        assert "userAuthorization," in chat
        assert "recoverySearch," in chat
        assert "!excludedDomains.has(candidateDomain)" in chat
        assert "const sources = readableSources;" in chat
        assert "if (sources.length < 1)" in chat


def test_edge_generate_uses_navigation_recovery_and_date_time_single_authoritative_source():
    for path in EDGE_FILES:
        source = path.read_text(encoding="utf-8")
        start = source.index('if (path === "/v1/ai/generate" && req.method === "POST")')
        end = source.index('if (path === "/v1/web/status"', start)
        generate = source[start:end]
        assert 'const requiredReadableSources = requestedReadableSourceCount(searchQuery)' in generate
        assert "if (edgeCurrentDateTimeQuery(query)) return 1;" in source
        assert 'const recoverySearch = await edgeSearch(' in generate
        assert '!excludedDomains.has(hostname)' in generate
        assert 'fallback_path: "railway-recovery"' in generate

def test_web_recovery_accepts_one_readable_source_without_false_empty() -> None:
    for path in EDGE_FILES:
        source = path.read_text(encoding="utf-8")
        chat_start = source.index('if (path === "/v1/chat/stream" && req.method === "POST")')
        chat_end = source.index("if (!edgeAIConfigured())", chat_start)
        chat = source[chat_start:chat_end]
        generate_start = source.index('if (path === "/v1/ai/generate" && req.method === "POST")')
        generate_end = source.index('if (path === "/v1/web/status"', generate_start)
        generate = source[generate_start:generate_end]
        assert "if (sources.length < 1)" in chat
        assert "if (!search.ok || sources.length < 1)" in generate
        assert 'level: sources.length >= 2 ? "multi-source" : "single-source"' in chat
        assert 'level: sources.length >= 2 ? "multi-source" : "single-source"' in generate
        assert "ok: true," in chat
        assert "ok: true," in generate

    android_api = (REPO_ROOT / "android/app/src/main/java/cl/caggrometal/deep33/Deep33Api.kt").read_text(encoding="utf-8")
    assert '(payload.optJSONArray("sources")?.length() ?: 0) >= 1' in android_api



def test_current_chile_news_preserves_topic_and_rejects_unrelated_headlines():
    import re

    prompt = (
        "Busca en Internet información actualizada a hoy sobre los principales "
        "acontecimientos de Chile. Consulta al menos 3 sitios web distintos, "
        "abre las páginas y extrae información directamente de ellas."
    )
    topic_pattern = re.compile(
        r"\b((?:(?:principales?|más\s+importantes|mayores?|recientes?|actuales?)\s+)?"
        r"(?:acontecimientos?|eventos?|noticias?|hechos?|sucesos?)"
        r"(?:\s+actuales?)?\s+(?:de\s+)?Chile)\b",
        re.IGNORECASE,
    )
    match = topic_pattern.search(prompt)
    assert match is not None
    assert re.sub(r"\s+", " ", match.group(1)).strip().lower() == "principales acontecimientos de chile"

    for path in EDGE_FILES:
        source = path.read_text(encoding="utf-8")
        query_start = source.index("function buildWebSearchQuery(")
        query_end = source.index("function requiresFreshWeb(", query_start)
        query_builder = source[query_start:query_end]
        assert "const chileTopicMatch = query.match(" in query_builder
        assert 'return chileTopic ? chileTopic + " hoy" : "noticias de Chile hoy";' in query_builder

        filter_start = source.index("function edgeFilterSearchResults(")
        filter_end = source.index("function edgeProvidersForResults(", filter_start)
        result_filter = source[filter_start:filter_end]
        assert "const chileNewsIntent = currentNews &&" in result_filter
        assert "const mentionsChileContext = explicitChileContext.test(articleEvidence);" in result_filter
        assert "return mentionsChileContext || ChileanNationalSection;" in result_filter
        # A publisher hosted on .cl is not, by itself, accepted as local-news evidence.
        assert 'publisherHost.endsWith(".cl")' in result_filter
        assert "const googleNewsStory = host === \"news.google.com\"" in result_filter
        assert "const ChileanNationalSection = !googleNewsStory" in result_filter
        # Google News wraps every story in one URL host; diversity and fetch scheduling
        # must use the actual publisher domain from RSS metadata.
        assert "const publisherDomain = String(result.source_domain || \"\").trim()" in source
        assert "distinct_domains: new Set(finalResults.map((item) =>" in source
        assert "const effectiveDomain = isSearchWrapper(canonicalUrl) && publisherHost" in source
        assert "source_domain: String(item.source_domain ?? \"\").slice(0, 255)" in source
        assert "const MIN_REALTIME_NEWS_RESULTS = 3;" in source
        assert "finalResults.length < MIN_REALTIME_NEWS_RESULTS" in source
        assert "expected_min_results: MIN_REALTIME_NEWS_RESULTS" in source
