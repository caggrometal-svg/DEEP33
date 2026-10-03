from __future__ import annotations

from pathlib import Path

from backend import main


ROOT = Path(__file__).resolve().parents[1]


def test_chat_stream_uses_live_streaming_path():
    source = (ROOT / "backend" / "main.py").read_text(encoding="utf-8")
    route = source[source.index('@app.post("/v1/chat/stream")'):]
    assert "body = stream_gateway(" in route
    assert '_sse_text_chunks(result["text"])' not in route


def test_stable_chat_skips_web_search():
    assert main.should_force_web([{"role": "user", "content": "2x2?"}]) is False


def test_live_external_request_requires_web():
    assert main.should_force_web([{"role": "user", "content": "¿Cuál es el precio actual del dólar?"}]) is True


def test_complexity_router_has_fast_balanced_deep_modes():
    assert main.complexity_profile([{"role": "user", "content": "2x2?"}])[2] == "FAST"
    assert main.complexity_profile([{"role": "user", "content": "Compara estas dos arquitecturas"}])[2] == "BALANCED"
    assert main.complexity_profile([{"role": "user", "content": "Explica todo paso a paso y en profundidad"}])[2] == "DEEP"


def test_android_stream_checkpoint_is_throttled():
    source = (
        ROOT
        / "android"
        / "app"
        / "src"
        / "main"
        / "java"
        / "cl"
        / "caggrometal"
        / "deep33"
        / "Deep33GenerationService.kt"
    ).read_text(encoding="utf-8")
    assert "now - lastCheckpointAt >= 500L" in source
    assert "checkpoint.length - lastCheckpointChars >= 1200" in source


def test_fast_turn_skips_remote_memory_unless_memory_is_requested():
    assert main.requires_memory_context([{"role": "user", "content": "2x2?"}], "FAST") is False
    assert main.requires_memory_context([{"role": "user", "content": "¿Qué sabes de mí?"}], "FAST") is True
    assert main.requires_memory_context([{"role": "user", "content": "2x2?"}], "BALANCED") is True


def test_gateway_uses_persistent_connection_pool():
    source = (ROOT / "backend" / "gateway.py").read_text(encoding="utf-8")
    assert "self._http_client" in source
    assert "httpx.Limits(" in source
    assert "max_keepalive_connections=10" in source


def test_phase_11_performance_trace_contract_exists():
    source = (ROOT / "backend" / "performance.py").read_text(encoding="utf-8")
    for marker in (
        "T2_BACKEND_RECEIVED",
        "T3_CONTEXT_PREPARED",
        "T4_SEARCH_STARTED",
        "T5_SEARCH_FINISHED",
        "T6_INFERENCE_STARTED",
        "T7_FIRST_TOKEN",
        "T8_STREAM_FINISHED",
        "T9_PERSISTENCE_FINISHED",
        "p50_ms",
        "p95_ms",
    ):
        assert marker in source


def test_android_emits_all_visible_latency_markers():
    service = (ROOT / "android" / "app" / "src" / "main" / "java" / "cl" / "caggrometal" / "deep33" / "Deep33GenerationService.kt").read_text(encoding="utf-8")
    activity = (ROOT / "android" / "app" / "src" / "main" / "java" / "cl" / "caggrometal" / "deep33" / "MainActivity.kt").read_text(encoding="utf-8")
    assert "PERF T1_REQUEST_SENT" in service
    assert "PERF T0_INPUT" in activity
    assert "PERF T10_FIRST_VISIBLE" in activity
    assert "PERF T11_FIRST_SPOKEN" in activity
