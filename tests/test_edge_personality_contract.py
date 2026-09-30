from pathlib import Path


EDGE_FILE = (
    Path(__file__).resolve().parents[1]
    / "supabase"
    / "functions"
    / "deep33-proxy"
    / "index.ts"
)


def _source() -> str:
    return EDGE_FILE.read_text(encoding="utf-8")


def test_edge_gateway_contains_all_four_personality_profiles() -> None:
    source = _source()
    for personality in ("AGRESIVO", "NEUTRO", "COMICO", "CONSPIRANOICO"):
        assert personality in source
    assert "function personalityInstruction" in source
    assert "const profiles: Record<string, string>" in source


def test_edge_gateway_keeps_personality_signatures_distinct() -> None:
    source = _source()
    for signature in (
        "SIGNATURE=direct pressure",
        "SIGNATURE=calm precision",
        "SIGNATURE=brief wit",
        "SIGNATURE=frame-independent reasoning",
    ):
        assert signature in source


def test_edge_gateway_has_conspiranoid_reasoning_protocol_and_self_falsification() -> None:
    source = _source()
    assert "CONSPIRANOICO REASONING PROTOCOL" in source
    assert "MAP THE FRAME" in source
    assert "EXPAND THE SEARCH SPACE" in source
    assert "CHECK ALTERNATIVES" in source
    assert "PRESERVE UNCERTAINTY" in source
    assert "Never fabricate facts, sources, events, documents, experiments or observations." in source
    assert '(selected === "CONSPIRANOICO" ? "\\n" + CONSPIRANOICO_REASONING_PROTOCOL : "")' in source


def test_edge_gateway_keeps_aggressive_chilean_register_controlled() -> None:
    source = _source()
    assert "weón" in source
    assert "cagaste" in source
    assert "No conviertas las groserías en muletillas" in source
    assert "No ataques por identidad, origen o condición" not in source
    assert "ni ataques por identidad, origen o condición" in source
