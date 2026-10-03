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


def test_model_router_respects_complexity_profiles():
    assert main.model_for_profile(None, "FAST") == (main.os.getenv("AI_GATEWAY_MODEL_FAST", "") or main.AI_GATEWAY_MODEL)
    assert main.model_for_profile(None, "BALANCED") == (main.os.getenv("AI_GATEWAY_MODEL_BALANCED", "") or main.AI_GATEWAY_MODEL)
    assert main.model_for_profile("explicit-model", "DEEP") == "explicit-model"


def test_web_fetch_is_optional_when_snippets_are_sufficient(monkeypatch):
    async def fake_search(query, **kwargs):
        return {
            "ok": True,
            "results": [
                {"title": "A", "url": "https://a.example/", "snippet": "A" * 140},
                {"title": "B", "url": "https://b.example/", "snippet": "B" * 140},
            ],
        }

    async def forbidden_fetch(*_args, **_kwargs):
        raise AssertionError("fetch should not run when snippets are sufficient")

    monkeypatch.setattr(main, "search_web", fake_search)
    monkeypatch.setattr(main, "fetch_page", forbidden_fetch)

    import asyncio
    working, sources, evidence, results = asyncio.run(
        main.prepare_web_evidence(
            [{"role": "user", "content": "¿Cuál es la situación actual?"}],
            request_id="fetch-optional",
            deep=False,
        )
    )
    assert len(sources) == 2
    assert len(results) == 2
    assert evidence
    assert any("Server-side web evidence" in str(item.get("content")) for item in working)

def test_full_end_to_end_trace_contract_has_all_markers():
    expected = (
        "T0_INPUT",
        "T1_REQUEST_SENT",
        "T2_BACKEND_RECEIVED",
        "T3_CONTEXT_PREPARED",
        "T4_SEARCH_STARTED",
        "T5_SEARCH_FINISHED",
        "T6_INFERENCE_STARTED",
        "T7_FIRST_TOKEN",
        "T8_STREAM_FINISHED",
        "T9_PERSISTENCE_FINISHED",
        "T10_FIRST_VISIBLE",
        "T11_FIRST_SPOKEN",
    )
    assert main.performance.STAGES == expected


def test_performance_trace_exposes_backend_latency_metrics():
    request_id = "phase0-contract"
    for stage in main.performance.STAGES[2:10]:
        main.performance.mark(request_id, stage)

    trace = main.performance.trace_snapshot(request_id)
    snapshot = main.performance.snapshot()

    assert trace["completed_stage_count"] == 8
    assert trace["expected_stage_count"] == 12
    assert "ttft_ms" in snapshot["summary"]
    assert "backend_total_ms" in snapshot["summary"]
    assert "p50_ms" in snapshot["summary"]["backend_total_ms"]
    assert "p95_ms" in snapshot["summary"]["backend_total_ms"]



def test_gateway_latency_snapshot_reports_learned_ttft_and_throughput():
    from backend.gateway import AIGateway, GatewayConfig, GatewayProvider

    providers = (
        GatewayProvider("p1", "https://p1.example", "https://p1.example/health", "", "m1"),
        GatewayProvider("p2", "https://p2.example", "https://p2.example/health", "", "m2"),
    )
    gateway_instance = AIGateway(GatewayConfig(providers=providers, timeout_seconds=5.0))
    for value in (100.0, 110.0, 90.0):
        gateway_instance._record_latency(providers[0], value)
    for value in (300.0, 310.0, 290.0):
        gateway_instance._record_latency(providers[1], value)
    for value in (20.0, 25.0, 30.0):
        gateway_instance._record_ttft(providers[0], value)
    for value in (20.0, 22.0, 24.0):
        gateway_instance._record_throughput(providers[0], value)

    snapshot = gateway_instance.latency_snapshot()
    assert snapshot["p1"]["p50_ms"] is not None
    assert snapshot["p1"]["ttft_p50_ms"] is not None
    assert snapshot["p1"]["tokens_per_second_p50"] is not None
    assert gateway_instance._ordered_providers()[0].name == "p1"
