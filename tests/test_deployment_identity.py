from __future__ import annotations

import backend.main as app


def test_vercel_deployed_branch_takes_precedence_over_stale_legacy_branch(monkeypatch):
    monkeypatch.setenv("VERCEL_GIT_COMMIT_REF", "fix/universal-web-contract-271026")
    monkeypatch.setenv("RENDER_GIT_BRANCH", "main")
    monkeypatch.setenv("DEEP33_GIT_BRANCH", "configured-old-branch")

    assert app._resolve_git_branch() == "fix/universal-web-contract-271026"


def test_configured_git_branch_is_used_when_vercel_ref_is_absent(monkeypatch):
    monkeypatch.delenv("VERCEL_GIT_COMMIT_REF", raising=False)
    monkeypatch.delenv("DEEP33_GIT_BRANCH", raising=False)
    monkeypatch.setenv("RENDER_GIT_BRANCH", "legacy-branch")

    assert app._resolve_git_branch() == "legacy-branch"


def test_railway_deployed_sha_takes_precedence_over_stale_configured_sha(monkeypatch):
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "railway-current-sha")
    monkeypatch.setenv("RENDER_GIT_COMMIT", "render-old-sha")
    monkeypatch.setenv("DEEP33_GIT_SHA", "configured-old-sha")

    assert app._resolve_git_sha() == "railway-current-sha"


def test_render_sha_fallback_remains_compatible(monkeypatch):
    monkeypatch.delenv("RAILWAY_GIT_COMMIT_SHA", raising=False)
    monkeypatch.setenv("RENDER_GIT_COMMIT", "render-current-sha")
    monkeypatch.setenv("DEEP33_GIT_SHA", "configured-old-sha")

    assert app._resolve_git_sha() == "render-current-sha"
