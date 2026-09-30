from backend.main import sanitize_assistant_text


def test_public_text_drops_links():
    cleaned = sanitize_assistant_text("Texto https://example.com/demo")
    assert "https://example.com/demo" not in cleaned


def test_public_text_drops_source_section():
    cleaned = sanitize_assistant_text("Resultado.\n\nFuentes:\n- example")
    assert cleaned == "Resultado."


def test_public_text_keeps_normal_prose():
    text = "DEEP33 separa hechos de incertidumbre."
    assert sanitize_assistant_text(text) == text
