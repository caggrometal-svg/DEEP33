from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ANDROID = ROOT / "android/app/src/main/java/cl/caggrometal/deep33"


def test_location_context_contains_no_exact_coordinates():
    source = (ANDROID / "Deep33LocationProvider.kt").read_text(encoding="utf-8")
    start = source.index("fun asPromptContext")
    end = source.index("    }", start)
    block = source[start:end]
    assert "latitude" not in block
    assert "longitude" not in block


def test_persisted_state_has_legacy_coordinate_scrubbing():
    source = (ANDROID / "SessionStore.kt").read_text(encoding="utf-8")
    assert "latitude" in source.lower()
    assert "longitude" in source.lower()
    assert "coordenada eliminada por privacidad" in source
    assert "migrateGpsPrivacyState()" in source


def test_generation_persists_conversation_snapshot_without_inference_payload():
    source = (ANDROID / "Deep33GenerationService.kt").read_text(encoding="utf-8")
    assert "JSONArray(pending.conversationJson)" in source
    assert "for (i in 0 until payload.length())" not in source
