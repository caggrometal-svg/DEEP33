from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ANDROID = ROOT / "android/app/src/main/java/cl/caggrometal/deep33"
BACKEND = ROOT / "backend/main.py"


def test_feedback_is_sent_outside_ui_thread_and_is_hashed():
    source = (ANDROID / "MainActivity.kt").read_text(encoding="utf-8")
    assert "sendResponseFeedback" in source
    assert 'MessageDigest.getInstance("SHA-256")' in source
    assert "executor.execute" in source


def test_feedback_is_validated_and_owned_by_authenticated_user():
    source = BACKEND.read_text(encoding="utf-8")
    assert 'path === "/v1/feedback"' in source
    assert "response_hash" in source
    assert '"positive" in source and "negative" in source'
    assert "ownerUserId" in source
    assert "deep33_feedback" in source
