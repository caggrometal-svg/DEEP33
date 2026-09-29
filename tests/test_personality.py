from __future__ import annotations

from backend.main import DEFAULT_PERSONALITY, PERSONALITIES, normalize_personality, personality_prompt


def test_personality_catalog_has_required_profiles() -> None:
    assert set(PERSONALITIES) == {"AGRESIVO", "NEUTRO", "COMICO", "CONSPIRANOICO"}
    assert DEFAULT_PERSONALITY == "NEUTRO"


def test_invalid_personality_falls_back_to_neutral() -> None:
    assert normalize_personality("") == "NEUTRO"
    assert normalize_personality("desconocido") == "NEUTRO"


def test_personality_prompt_is_style_only() -> None:
    conspiranoic = personality_prompt("CONSPIRANOICO")
    assert "CONSPIRANOICO" in conspiranoic
    assert "active personality contract" in conspiranoic.lower()
    assert "must not silently fall back to NEUTRO" in conspiranoic
    assert "higher-priority system rules" in conspiranoic

    comic = personality_prompt("COMICO")
    assert "COMICO" in comic
    assert "humor oscuro" in comic
    assert "humor atrevido" in comic
    assert "categoría protegida" in comic


def test_personality_prompt_rejects_unknown_to_neutral() -> None:
    prompt = personality_prompt("unknown")
    assert "NEUTRO" in prompt
