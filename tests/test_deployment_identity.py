from __future__ import annotations

import backend.main as app


def test_vercel_deployed_sha_takes_precedence_over_configured_sha(monkeypatch):
    monkeypatch.setenv("VERCEL_GIT_COMMIT_SHA", "vercel-current-sha")
    monkeypatch.setenv("DEEP33_GIT_SHA", "configured-old-sha")

    assert app._resolve_git_sha() == "vercel-current-sha"


def test_configured_sha_is_used_only_without_vercel_metadata(monkeypatch):
    monkeypatch.delenv("VERCEL_GIT_COMMIT_SHA", raising=False)
    monkeypatch.setenv("DEEP33_GIT_SHA", "configured-sha")

    assert app._resolve_git_sha() == "configured-sha"
