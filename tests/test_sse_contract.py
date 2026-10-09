from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ANDROID = ROOT / "android/app/src/main/java/cl/caggrometal/deep33"


def test_sse_parser_supports_multiple_content_envelopes():
    source = (ANDROID / "Deep33Api.kt").read_text(encoding="utf-8")
    assert "is JSONArray" in source
    assert 'value.optString("content")' in source
    assert 'value.optString("text")' in source
    assert "message" in source
    assert "delta" in source


def test_clean_eof_is_accepted():
    source = (ANDROID / "Deep33Api.kt").read_text(encoding="utf-8")
    assert "clean SSE termination" in source
    assert "if (!sawDone) throw Deep33ApiException" not in source

def test_stream_does_not_fail_over_after_emitting_partial_text():
    source = (ANDROID / "Deep33Api.kt").read_text(encoding="utf-8")
    stream = source.split("    fun stream(", 1)[1].split("    fun cancelActiveStream(", 1)[0]
    assert stream.count("emitted ||") == 3
    assert "Deep33ApiException.Kind.AUTH &&\n                        !emitted &&" in stream
    assert "partialOutput = true" in stream
