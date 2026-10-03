from __future__ import annotations

import backend.main as main


def test_deep33_canonical_identity_is_explicit() -> None:
    prompt = main.personality_prompt("AGRESIVO")

    assert "OFFICIAL AI IDENTITY: The intelligence speaking in this conversation is DEEP33" in prompt
    assert "MANUFACTURER / CREATOR: Camilo Aggro" in prompt
    assert "DEEP33 IS A DISTINCT PRODUCT INTELLIGENCE" in prompt
    assert "Do not collapse its identity into the upstream model" in prompt


def test_base_model_brands_cannot_replace_deep33_identity() -> None:
    prompt = main.personality_prompt("NEUTRO")

    for blocked in ("Gemma", "Gemini", "Google", "Google DeepMind", "OpenAI", "Kilo"):
        assert f"Never claim that DEEP33 was developed, created, manufactured, or is itself by {blocked}" in prompt


def test_runtime_identity_is_separated_from_product_identity() -> None:
    prompt = main.personality_prompt("CONSPIRANOICO")

    assert "TECHNICAL IDENTITY SEPARATION" in prompt
    assert "implementation detail only" in prompt
    assert "verified runtime metadata" in prompt
    assert "Preserve this identity across conversations, personalities, web evidence, memory, and failover paths" in prompt
