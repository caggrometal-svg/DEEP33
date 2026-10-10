from __future__ import annotations

from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
EDGE_PATHS = (
    ROOT / "supabase" / "functions" / "deep33-proxy" / "index.ts",
    ROOT / "supabase" / "functions" / "deep33-tertiary" / "index.ts",
)


@pytest.mark.parametrize("path", EDGE_PATHS)
def test_free_inference_fallbacks_are_opt_in_and_use_server_secrets(path: Path) -> None:
    source = path.read_text(encoding="utf-8")

    # No provider is added unless an operator explicitly enables the feature.
    assert 'DEEP33_ENABLE_FREE_INFERENCE_FALLBACKS' in source
    assert '.toLowerCase() !== "true"' in source
    assert 'DEEP33_CLOUDFLARE_ACCOUNT_ID' in source
    assert 'DEEP33_CLOUDFLARE_API_TOKEN' in source
    assert 'DEEP33_GROQ_API_KEY' in source
    assert 'api_key: cloudflareToken' in source
    assert 'api_key: groqKey' in source

    # Keys are read from Edge Function environment secrets, never embedded in source.
    assert 'https://api.groq.com/openai/v1/chat/completions' in source
    assert 'https://api.cloudflare.com/client/v4/accounts/' in source
    assert 'Authorization' in source


@pytest.mark.parametrize("path", EDGE_PATHS)
def test_free_inference_fallbacks_fail_fast_before_render(path: Path) -> None:
    source = path.read_text(encoding="utf-8")

    add_fallbacks = source.index("addFreeInferenceProviders(providers);")
    render_fallback = source.index(
        'if (!providers.some((provider) => provider.name === "render-backend-fallback"))'
    )
    assert add_fallbacks < render_fallback

    # Synchronous requests to optional providers have a shorter deadline than the main provider.
    assert "FREE_INFERENCE_REQUEST_TIMEOUT_MS = 7000" in source
    assert "isFreeInferenceProvider(provider)" in source
    assert "FREE_INFERENCE_FIRST_CHUNK_TIMEOUT_MS = 4000" in source


def test_cloudflare_fallback_fails_fast_when_capacity_is_busy() -> None:
    for path in EDGE_PATHS:
        source = path.read_text(encoding="utf-8")
        assert "rejectIfBusy: true" in source
        assert 'name: "cloudflare-workers-ai-free"' in source
        assert 'name: "groq-free"' in source


def test_free_provider_does_not_start_a_second_generation_after_stream_output() -> None:
    source = EDGE_PATHS[0].read_text(encoding="utf-8")
    stream_section = source[
        source.index("async function streamEdgeAI(") :
        source.index("\nasync function ", source.index("async function streamEdgeAI(") + 1)
    ]
    assert "if (emitted)" in stream_section
    assert "throw new Error(lastError)" in stream_section
