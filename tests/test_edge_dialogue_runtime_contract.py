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
