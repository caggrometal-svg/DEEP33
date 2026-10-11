from __future__ import annotations

from backend.main import cache_key, scope_session_for_user


def test_raw_render_and_edge_scoped_session_share_idempotency_namespace() -> None:
    user_id = "3dadf20a-dd72-431f-aa8b-e0a524e5e44c"
    client_session_id = "android-e2e-23c1a694-450e-4e5f-807d-2a4830ceab29"
    edge_session_id = f"{user_id}:{client_session_id}"

    direct_session = scope_session_for_user(user_id, client_session_id)
    edge_session = scope_session_for_user(user_id, edge_session_id)

    assert direct_session == edge_session == edge_session_id
    assert cache_key(direct_session, "request-123") == cache_key(edge_session, "request-123")


def test_scoped_session_is_not_prefixed_twice() -> None:
    user_id = "3dadf20a-dd72-431f-aa8b-e0a524e5e44c"
    scoped_session = f"{user_id}:android-e2e-session"

    assert scope_session_for_user(user_id, scoped_session) == scoped_session


def test_direct_session_scope_matches_edge_length_limit() -> None:
    user_id = "3dadf20a-dd72-431f-aa8b-e0a524e5e44c"
    raw_session = "s" * 140
    edge_equivalent = f"{user_id}:{raw_session[:96]}"[:128]

    assert scope_session_for_user(user_id, raw_session) == edge_equivalent
    assert scope_session_for_user(user_id, edge_equivalent) == edge_equivalent
