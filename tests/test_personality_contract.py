from backend.main import (
    DEEP33_IDENTITY_CORE,
    PERSONALITIES,
    personality_prompt,
)


def test_all_personalities_have_distinct_runtime_contracts():
    prompts = {name: personality_prompt(name) for name in PERSONALITIES}
    assert set(prompts) == {"AGRESIVO", "NEUTRO", "COMICO", "CONSPIRANOICO"}
    assert len(set(prompts.values())) == 4
    for name, prompt in prompts.items():
        assert f"ACTIVE_PERSONALITY={name}" in prompt
        assert DEEP33_IDENTITY_CORE in prompt
        assert "MODE SIGNATURE" in prompt
        assert "Make at least two traits" in prompt


def test_invalid_personality_is_deterministically_neutral():
    assert "ACTIVE_PERSONALITY=NEUTRO" in personality_prompt("not-a-mode")
