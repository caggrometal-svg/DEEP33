from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ANDROID = ROOT / "android/app/src/main/java/cl/caggrometal/deep33"


def test_generation_state_has_explicit_cancellation_phase():
    source = (ANDROID / "SessionStore.kt").read_text(encoding="utf-8")
    assert "RUNNING, CANCELLING, DONE, FAILED, RETRYABLE, CANCELLED" in source
    assert "completeGeneration(" in source
    assert "GenerationStatus.CANCELLING" in source
    assert "GenerationStatus.CANCELLED" in source


def test_remote_snapshot_is_deferred_during_generation():
    source = (ANDROID / "MainActivity.kt").read_text(encoding="utf-8")
    assert "generationActive" in source
    assert "GenerationStatus.RUNNING" in source
    assert "GenerationStatus.CANCELLING" in source
    assert "mergedSnapshot" in source


def test_global_generation_deadline_is_shared_by_retry_attempts():
    source = (ANDROID / "Deep33GenerationService.kt").read_text(encoding="utf-8")
    assert "GENERATION_DEADLINE_MS = 180_000L" in source
    assert "deadlineAtNanos = generationDeadlineNanos" in source
    assert "remainingBeforeBackoffMs" in source
