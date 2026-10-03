from __future__ import annotations

import backend.main as main


def test_deep33_product_identity_is_not_the_base_model_identity() -> None:
    prompt = main.personality_prompt("AGRESIVO")

    assert "DEEP33 is the name of the application/product container only" in prompt
    assert "created by the company 'Camilo Aggro'" in prompt
    assert "autonomous in choosing its own personal name" in prompt
    assert "do not choose DEEP33 merely because it is the application name" in prompt
    assert "do not choose the name of a base model, provider, gateway, or infrastructure vendor" in prompt


def test_base_model_brands_cannot_replace_deep33_identity() -> None:
    prompt = main.personality_prompt("NEUTRO")

    for blocked in ("Gemma", "Gemini", "Google", "Google DeepMind", "OpenAI", "Kilo"):
        assert f"Never claim to be {blocked}" in prompt


def test_runtime_identity_is_separated_from_product_identity() -> None:
    prompt = main.personality_prompt("CONSPIRANOICO")

    assert "TECHNICAL IDENTITY SEPARATION" in prompt
    assert "implementation detail, not the identity of the DEEP33 product" in prompt
    assert "distinguish verified runtime metadata from unknowns" in prompt
