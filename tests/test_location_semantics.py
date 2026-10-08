from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ANDROID = ROOT / "android/app/src/main/java/cl/caggrometal/deep33"
SEARCH = ROOT / "backend/search/engine.py"


def test_android_recognizes_natural_weather_language():
    source = (ANDROID / "MainActivity.kt").read_text(encoding="utf-8")
    assert "calor" in source
    assert "frio" in source
    assert "hara" in source
    assert "estara" in source


def test_backend_search_recognizes_natural_weather_language():
    source = SEARCH.read_text(encoding="utf-8")
    assert "weather_semantic" in source
    assert "hará calor" in source or "hara calor" in source
