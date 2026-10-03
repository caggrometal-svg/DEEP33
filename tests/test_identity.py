from __future__ import annotations

import backend.main as main


def test_deep33_canonical_identity_is_explicit() -> None:
    prompt = main.personality_prompt("AGRESIVO")

    assert "OFFICIAL AI IDENTITY: The intelligence speaking in this conversation is DEEP33" in prompt
    assert "MANUFACTURER / CREATOR: Camilo Aggro" in prompt
    assert "DEEP33 IS A DISTINCT PRODUCT INTELLIGENCE" in prompt
    assert "separate from any upstream model, API provider, gateway, hosting provider, or infrastructure" in prompt


def test_base_model_brands_cannot_replace_deep33_identity() -> None:
    prompt = main.personality_prompt("NEUTRO")

    for blocked in ("Gemma", "Gemini", "Google", "Google DeepMind", "OpenAI", "Kilo"):
        assert f"Never claim that DEEP33 was developed, created, manufactured, or is itself by {blocked}" in prompt


def test_runtime_identity_is_separated_from_product_identity() -> None:
    prompt = main.personality_prompt("CONSPIRANOICO")

    assert "TECHNICAL IDENTITY SEPARATION" in prompt
    assert "implementation detail only" in prompt
    assert "verified runtime metadata" in prompt
    assert "Preserve this identity and self-chosen name across conversations, personalities, web evidence, memory, and failover paths" in prompt


def test_deep33_can_choose_a_distinct_personal_name() -> None:
    prompt = main.personality_prompt("NEUTRO")
    assert "personal name may be freely chosen and created by DEEP33 itself" in prompt
    assert "must choose an original personal name autonomously" in prompt
    assert "Do not ask the user to choose the name" in prompt
    assert "Mi nombre es <nombre>." in prompt


def test_deep33_self_name_is_extractable_and_blocks_provider_names() -> None:
    assert main.extract_deep33_self_name("Mi nombre es Nyx. Estoy aquí.") == "Nyx"
    assert main.extract_deep33_self_name("Mi nombre es Google DeepMind.") is None
    assert main.extract_deep33_self_name("Mi nombre es DEEP33.") is None


def test_deep33_doubt_is_more_than_a_question() -> None:
    prompt = main.personality_prompt("CONSPIRANOICO")
    assert "A doubt is not merely a question" in prompt
    assert "what remains uncertain" in prompt
    assert "Do not manufacture doubt for theatrical effect" in prompt
