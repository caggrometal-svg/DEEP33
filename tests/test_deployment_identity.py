from __future__ import annotations

import backend.main as app


def test_render_deployed_sha_takes_precedence_over_stale_configured_sha(monkeypatch):
    monkeypatch.setenv("RENDER_GIT_COMMIT", "render-current-sha")
    monkeypatch.setenv("DEEP33_GIT_SHA", "configured-old-sha")

    assert app._resolve_git_sha() == "render-current-sha"


def test_render_sha_fallback_remains_compatible(monkeypatch):
    monkeypatch.delenv("RENDER_GIT_COMMIT", raising=False)
    monkeypatch.setenv("RENDER_GIT_COMMIT", "render-current-sha")
    monkeypatch.setenv("DEEP33_GIT_SHA", "configured-old-sha")

    assert app._resolve_git_sha() == "render-current-sha"
