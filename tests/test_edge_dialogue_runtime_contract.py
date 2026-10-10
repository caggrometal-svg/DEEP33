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


def test_edge_provider_fallback_url_is_declared_before_use() -> None:
    for path in EDGE_FILES:
        source = path.read_text(encoding="utf-8")
        if "EDGE_AI_UPSTREAM_URL" in source:
            assert 'const EDGE_AI_UPSTREAM_URL = "https://deep33-backend.onrender.com";' in source


def test_realtime_search_does_not_discard_all_undated_current_results() -> None:
    for path in EDGE_FILES:
        source = path.read_text(encoding="utf-8")
        assert "const publicationDateUnknown = !publishedAt || !Number.isFinite(publicationTimestamp);" in source
        assert "googleNewsStory || publicationDateUnknown" in source


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


def test_critical_render_fallback_is_not_skipped_by_open_provider_circuit() -> None:
    for path in EDGE_FILES:
        source = path.read_text(encoding="utf-8")
        assert "const criticalRenderFallback =" in source
        assert 'provider.name === "render-backend-fallback"' in source
        assert "Date.now() < circuit.openUntil && !criticalRenderFallback" in source


def test_explicit_inference_probe_passes_scoped_session_to_render_fallback() -> None:
    for path in EDGE_FILES:
        source = path.read_text(encoding="utf-8")
        assert "}, requestId, null, userAuthorization, sessionId);" in source


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
