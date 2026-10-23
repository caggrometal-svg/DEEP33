from backend import main


def test_sanitize_generation_output_preserves_only_safe_https_sources():
    safe = {
        "title": "Official documentation",
        "url": "https://docs.example.org/reference",
        "snippet": "Authoritative source content.",
        "provider": "bing",
    }
    output = {
        "result": {"role": "assistant", "text": "A grounded answer."},
        "sources": [
            safe,
            {"title": "Insecure", "url": "http://example.org/page"},
            {"title": "Credentials", "url": "https://user:secret@example.org/page"},
            safe,
        ],
        "web_navigation": True,
    }

    sanitized = main.sanitize_generation_output(output)

    assert sanitized["sources"] == [safe]
    assert sanitized["web_navigation"] is True


def test_sanitize_generation_output_rejects_navigation_claim_without_https_source():
    output = {
        "result": {"role": "assistant", "text": "A response without verified sources."},
        "sources": [
            {"title": "Insecure", "url": "http://example.org/page"},
            {"title": "Missing URL"},
        ],
        "web_navigation": True,
    }

    sanitized = main.sanitize_generation_output(output)

    assert sanitized["sources"] == []
    assert sanitized["web_navigation"] is False


def test_shared_generation_normalization_preserves_verified_sources():
    source = {
        "title": "Python documentation",
        "url": "https://docs.python.org/3/",
        "snippet": "Official Python documentation.",
        "provider": "bing",
    }
    request = main.ChatRequest(
        messages=[{"role": "user", "content": "What is Python?"}],
        model="test-model",
        personality="NEUTRO",
    )
    shared_record = {
        "text": "Python is a programming language.",
        "model": "test-model",
        "provider": "kilo",
        "sources": [source],
        "web_navigation": True,
    }

    normalized = main.normalize_shared_generation_output(
        shared_record,
        request,
        request_id="source-contract-test",
        personality="NEUTRO",
    )

    assert normalized["sources"] == [source]
    assert normalized["web_navigation"] is True
