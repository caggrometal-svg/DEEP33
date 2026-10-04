from __future__ import annotations

import backend.main as main


def test_no_artificial_output_length_rules_in_dialogue_policy():
    prompt = main.dialogue_policy_prompt([{"role": "user", "content": "¿Qué es HTTP?"}])
    assert "no uses un número fijo de frases, palabras o caracteres" in prompt.lower()
    assert "no recortes una precisión o explicación necesaria" in prompt


def test_personalities_have_maximally_distinct_behavioral_contracts():
    prompts = {name: main.personality_prompt(name) for name in main.PERSONALITIES}
    assert len(set(prompts.values())) == 4
    assert "presión intelectual" in prompts["AGRESIVO"]
    assert "ritmo estable" in prompts["NEUTRO"]
    assert "humor atrevido" in prompts["COMICO"]
    assert "No aceptes la explicación por defecto" in prompts["CONSPIRANOICO"]


def test_research_policy_rejects_authority_as_proof():
    prompt = main.dialogue_policy_prompt(
        [{"role": "user", "content": "¿La versión oficial es realmente cierta?"}]
    )
    assert "No trates una fuente oficial como verdad por autoridad" in prompt
    assert "incluye evidencia que apoye y que contradiga" in prompt
