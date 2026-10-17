from __future__ import annotations

import asyncio
import json
import os
import statistics
import time
from contextlib import suppress
from typing import Any

import httpx
import pytest

from backend.search.engine import SearchEngine, is_realtime_query
from backend.search.resilient import WebOrchestrator
from backend.search.resilience import (
    DiagnosticCode,
    NetworkBudgets,
    controller,
    graceful_payload,
)


QUERY_BATTERY = [
    "Python asyncio official documentation",
    "Presidente actual de Chile",
    "Clima actual en Santiago de Chile",
    "Últimas noticias económicas de Chile",
    "Últimos resultados de fútbol internacional",
    "World Health Organization dengue fact sheet",
    "NASA Artemis mission official",
]


@pytest.fixture(autouse=True)
def reset_resilience_state():
    original = controller.budgets
    controller.breakers.clear()
    yield
    controller.budgets = original
    controller.breakers.clear()


def test_realtime_intent_detector_forces_current_web_queries():
    samples = [
        "qué pasó hoy en Chile",
        "presidente actual de Chile",
        "últimas noticias económicas",
        "precio actual del dólar",
        "clima ahora en Santiago",
        "resultado del partido de hoy",
    ]
    assert all(is_realtime_query(value) for value in samples)


def test_graceful_payload_never_requires_http_5xx():
    payload = graceful_payload(
        ok=False,
        code=DiagnosticCode.PROVIDER_TIMEOUT,
        request_id="gate-1",
        errors=["bing:timeout"],
    )
    assert payload["ok"] is False
    assert payload["degraded"] is True
    assert payload["code"] == "PROVIDER_TIMEOUT"
    assert payload["results"] == []
    assert payload["sources"] == []


def test_t0_primary_and_tplus_fallback_recovers_without_orphans():
    async def scenario() -> None:
        controller.budgets = NetworkBudgets(
            connect_seconds=0.01,
            first_byte_seconds=0.02,
            provider_total_seconds=0.05,
            fallback_delay_seconds=0.02,
            global_search_seconds=0.30,
        )
        engine = SearchEngine(max_results=5, max_queries=1)

        async def fake_provider(
            provider: str,
            query: str,
            *,
            timeout_seconds: float,
            api_key: str,
        ) -> list[dict[str, Any]]:
            if provider in {"bing", "mojeek"}:
                await asyncio.sleep(0.20)
                return []
            if provider == "duckduckgo":
                return [{
                    "title": "Python asyncio official documentation",
                    "url": "https://docs.python.org/3/library/asyncio.html",
                    "snippet": "Python asyncio official documentation and library reference.",
                }]
            return []

        engine._provider_search = fake_provider  # type: ignore[method-assign]
        started = time.perf_counter()
        successful, errors, attempted = await engine._run_query(
            ["bing", "mojeek", "duckduckgo"],
            "Python asyncio official documentation",
            plan_depth="standard",
            timeout_seconds=0.30,
            api_key="",
            fast_mode=False,
        )
        elapsed = time.perf_counter() - started

        assert successful
        assert "duckduckgo" in attempted
        assert elapsed < 0.20
        assert not any("provider_task" in value for value in errors)
        await asyncio.sleep(0)
        dangling = [
            task
            for task in asyncio.all_tasks()
            if task is not asyncio.current_task()
            and not task.done()
            and (task.get_name() or "").startswith("web-search:")
        ]
        assert dangling == []

    asyncio.run(scenario())


def test_resilient_orchestrator_uses_one_semantic_recovery_wave_after_zero_evidence():
    async def scenario() -> None:
        orchestrator = WebOrchestrator(
            max_results=5,
            provider_total_seconds=0.5,
            global_budget_seconds=2.0,
            fallback_delay_seconds=0.0,
        )
        calls: list[tuple[str, str]] = []

        async def provider(name: str, query: str, timeout: float, limit: int) -> list[dict]:
            calls.append((name, query))
            if query == "python asyncio":
                return [{
                    "title": "Python asyncio documentation",
                    "url": f"https://{name}.example/python-asyncio",
                    "snippet": "Python asyncio library reference",
                }]
            return [{
                "title": "Python asyncio tutorial",
                "url": "https://single.example/python-asyncio",
                "snippet": "Python asyncio reference material",
            }]

        for provider_name in ("bing", "mojeek", "duckduckgo"):
            async def bound(query: str, timeout: float, limit: int, _name=provider_name):
                return await provider(_name, query, timeout, limit)
            orchestrator.providers[provider_name] = bound

        payload = await orchestrator.search(
            "Python asyncio official documentation",
            fresh=True,
        )

        assert payload["ok"] is True
        assert len({item["source_domain"] for item in payload["results"]}) >= 2
        recovery_calls = [item for item in calls if item[1] == "python asyncio"]
        assert {name for name, _ in recovery_calls} == {"bing", "mojeek", "duckduckgo"}

    asyncio.run(scenario())


def test_no_results_does_not_poison_resilient_provider_circuit():
    async def scenario() -> None:
        orchestrator = WebOrchestrator(
            max_results=5,
            provider_total_seconds=0.2,
            global_budget_seconds=1.0,
            fallback_delay_seconds=0.0,
        )

        async def empty_provider(query: str, timeout: float, limit: int) -> list[dict]:
            return []

        orchestrator.providers["bing"] = empty_provider
        for _ in range(8):
            outcome = await orchestrator._call_provider("bing", "topic with no matches")
            assert outcome.error_code == "NO_RESULTS"

        state = orchestrator.circuits["bing"]
        assert state.consecutive_failures == 0
        assert state.opened_until == 0.0

    asyncio.run(scenario())


def test_circuit_breaker_opens_after_five_failures_for_sixty_second_policy():
    async def scenario() -> None:
        controller.budgets = NetworkBudgets(
            provider_total_seconds=0.01,
            fallback_delay_seconds=0.01,
            global_search_seconds=0.10,
        )

        async def slow() -> list[dict[str, Any]]:
            await asyncio.sleep(1)
            return []

        for _ in range(5):
            _results, attempt = await controller.run_provider(
                "bing",
                slow,
                timeout_seconds=0.01,
            )
            assert attempt.ok is False

        results, attempt = await controller.run_provider(
            "bing",
            slow,
            timeout_seconds=0.01,
        )
        assert results == []
        assert attempt.code == DiagnosticCode.PROVIDER_BLOCKED
        breaker = await controller.breaker("bing")
        assert breaker.opened_at is not None
        assert breaker.open_seconds == 60.0

    asyncio.run(scenario())


def test_safe_task_consumes_background_exception():
    async def scenario() -> None:
        captured: list[dict[str, Any]] = []
        loop = asyncio.get_running_loop()
        previous = loop.get_exception_handler()
        loop.set_exception_handler(lambda _loop, context: captured.append(context))

        async def explode() -> None:
            raise RuntimeError("chaos")

        controller.safe_task(explode(), name="gate-safe-task")
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        loop.set_exception_handler(previous)
        assert not any(
            "Task exception was never retrieved" in str(item.get("message", ""))
            for item in captured
        )

    asyncio.run(scenario())



@pytest.mark.parametrize(
    ("failure", "expected_code"),
    [
        (RuntimeError("upstream returned HTTP 403"), "FETCH_BLOCKED"),
        (RuntimeError("upstream returned HTTP 500"), "FETCH_FAILED"),
        (TimeoutError("upstream timeout"), "FETCH_TIMEOUT"),
    ],
)
def test_web_fetch_partial_failures_are_http_200(monkeypatch, failure, expected_code):
    import backend.main as main
    from fastapi.testclient import TestClient

    async def allow(_request, _session_id):
        return None

    async def fail_fetch(*_args, **_kwargs):
        raise failure

    monkeypatch.setattr(main, "enforce_client_controls", allow)
    monkeypatch.setattr(main, "fetch_page", fail_fetch)
    monkeypatch.setattr(main, "DEEP33_WEB_TOOLS_ENABLED", True)

    response = TestClient(main.app).get(
        "/v1/web/fetch",
        params={"url": "https://example.com/article"},
        headers={
            "X-DEEP33-Session-Id": "global-gate-fetch",
            "X-Request-ID": "global-gate-fetch",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["degraded"] is True
    assert body["code"] == expected_code


@pytest.mark.parametrize(
    ("failure", "expected_code"),
    [
        (RuntimeError("provider timeout"), "PROVIDER_TIMEOUT"),
        (RuntimeError("NO_RESULTS"), "NO_RESULTS"),
        (RuntimeError("provider exploded"), "PROVIDER_ERROR"),
    ],
)
def test_web_search_partial_failures_are_http_200(monkeypatch, failure, expected_code):
    import backend.main as main
    from fastapi.testclient import TestClient

    async def allow(_request, _session_id):
        return None

    async def fail_search(*_args, **_kwargs):
        raise failure

    monkeypatch.setattr(main, "enforce_client_controls", allow)
    monkeypatch.setattr(main, "search_web", fail_search)
    monkeypatch.setattr(main, "DEEP33_WEB_TOOLS_ENABLED", True)

    response = TestClient(main.app).get(
        "/v1/web/search",
        params={"q": "Python asyncio official documentation"},
        headers={
            "X-DEEP33-Session-Id": "global-gate-search",
            "X-Request-ID": "global-gate-search",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["degraded"] is True
    assert body["code"] == expected_code


def test_prepare_web_evidence_requires_original_pages_and_injects_text(monkeypatch):
    import backend.main as main

    first_text = "ORIGINAL_PAGE_TEXT_ALPHA " + ("El editor publica datos concretos y una explicación verificable. " * 12)
    second_text = "ORIGINAL_PAGE_TEXT_BETA " + ("La segunda fuente incluye contexto independiente y detalles contrastables. " * 12)

    async def fake_search(*_args, **_kwargs):
        return {
            "ok": True,
            "provider": "test-search",
            "providers_attempted": ["test-search"],
            "results": [
                {
                    "title": "Fuente A",
                    "url": "https://alpha.example/article",
                    "snippet": "Snippet de búsqueda suficientemente largo. " * 5,
                    "provider": "test-search",
                },
                {
                    "title": "Fuente B",
                    "url": "https://beta.example/report",
                    "snippet": "Otro snippet de búsqueda suficientemente largo. " * 5,
                    "provider": "test-search",
                },
            ],
            "verification": {"distinct_domains": 2},
            "retrieved_at": "2026-10-15T00:00:00+00:00",
            "fresh_request": True,
        }

    async def fake_fetch(url: str, **_kwargs):
        text = first_text if "alpha.example" in url else second_text
        return {
            "ok": True,
            "url": url,
            "final_url": url,
            "title": "Original page",
            "text": text,
            "retrieved_at": "2026-10-15T00:00:01+00:00",
        }

    monkeypatch.setattr(main, "search_web", fake_search)
    monkeypatch.setattr(main, "fetch_page", fake_fetch)
    messages = [{
        "role": "user",
        "content": "Investiga y contrasta las fuentes originales sobre este tema.",
    }]

    working, sources, evidence_fragments, compact = asyncio.run(
        main.prepare_web_evidence(messages, request_id="fetch-context-test", deep=True)
    )

    assert working is not None
    injected_context = str(working[-1]["content"])
    assert "ORIGINAL_PAGE_TEXT_ALPHA" in injected_context
    assert "ORIGINAL_PAGE_TEXT_BETA" in injected_context
    assert compact["domains_used"] == 2
    assert compact["extracted_text_chars"] >= 160
    assert len(compact["fetched_pages"]) == 2
    assert any("ORIGINAL_PAGE_TEXT_ALPHA" in value for value in evidence_fragments)


def test_web_search_success_requires_real_extracted_page_text(monkeypatch, caplog):
    import backend.main as main
    from fastapi.testclient import TestClient

    async def allow(_request, _session_id):
        return None

    async def fake_search(*_args, **_kwargs):
        return {
            "ok": True,
            "provider": "test-search",
            "providers_attempted": ["test-search"],
            "results": [
                {
                    "title": "Fuente original",
                    "url": "https://publisher.example/article",
                    "snippet": "Snippet solamente; no cuenta como extracción real.",
                    "provider": "test-search",
                },
            ],
            "verification": {"distinct_domains": 1},
        }

    async def fake_fetch(url: str, **_kwargs):
        text = "REAL_EXTRACTED_PAGE_TEXT " + ("Contenido real de la página original. " * 12)
        return {
            "ok": True,
            "url": url,
            "final_url": url,
            "title": "Página extraída",
            "text": text,
            "retrieved_at": "2026-10-15T00:00:01+00:00",
        }

    monkeypatch.setattr(main, "enforce_client_controls", allow)
    monkeypatch.setattr(main, "search_web", fake_search)
    monkeypatch.setattr(main, "fetch_page", fake_fetch)
    monkeypatch.setattr(main, "DEEP33_WEB_TOOLS_ENABLED", True)

    response = TestClient(main.app).get(
        "/v1/web/search",
        params={"q": "Busca en internet el documento original"},
        headers={
            "X-DEEP33-Session-Id": "search-extraction-success",
            "X-Request-ID": "search-extraction-success",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["status"] == "success"
    assert payload["domains_used"] >= 1
    assert payload["extracted_text_chars"] >= 80
    assert payload["fetched_pages"]
    assert len(payload["fetched_pages"][0]["text"]) >= 80
    assert "REAL_EXTRACTED_PAGE_TEXT" in payload["fetched_pages"][0]["text"]


def test_web_search_fails_closed_when_search_or_extraction_is_not_valid(monkeypatch, caplog):
    import backend.main as main
    from fastapi.testclient import TestClient

    async def allow(_request, _session_id):
        return None

    async def fake_search(_query: str, **_kwargs):
        if _query == "no-domain":
            return {"ok": True, "results": [], "sources": [], "verification": {"distinct_domains": 0}}
        source = {
            "title": "Resultado",
            "url": f"https://publisher.example/{_query}",
            "snippet": "Snippet largo que no debe confundirse con texto extraído. " * 4,
            "provider": "test-provider",
        }
        return {
            "ok": _query != "search-not-ok",
            "provider": "test-provider",
            "results": [source],
            "sources": [source],
            "verification": {"distinct_domains": 1},
        }

    async def fake_fetch(url: str, **_kwargs):
        if "short-text" in url:
            return {"ok": True, "url": url, "final_url": url, "title": "Short", "text": "solo snippet"}
        return {
            "ok": True,
            "url": url,
            "final_url": url,
            "title": "Original",
            "text": "REAL_SOURCE_TEXT " + ("Texto extraído desde la página original. " * 12),
        }

    monkeypatch.setattr(main, "enforce_client_controls", allow)
    monkeypatch.setattr(main, "search_web", fake_search)
    monkeypatch.setattr(main, "fetch_page", fake_fetch)
    monkeypatch.setattr(main, "DEEP33_WEB_TOOLS_ENABLED", True)

    client = TestClient(main.app)
    for query in ("search-not-ok", "no-domain", "short-text"):
        response = client.get(
            "/v1/web/search",
            params={"q": query},
            headers={
                "X-DEEP33-Session-Id": f"search-fail-closed-{query}",
                "X-Request-ID": f"search-fail-closed-{query}",
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["ok"] is False
        assert payload["status"] == "failed"
        assert payload["status"] != "success"

    assert "reason=PARSER_FAIL" in caplog.text
    assert "request_id=search-fail-closed-short-text" in caplog.text
    assert "url=https://publisher.example/short-text" in caplog.text
    assert "provider=test-provider" in caplog.text


def _auth_headers(token: str) -> dict[str, str]:
    headers = {
        "Cache-Control": "no-cache",
        "X-DEEP33-Session-Id": f"global-gate-{int(time.time() * 1000)}",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


async def _live_search(client: httpx.AsyncClient, base_url: str, token: str, query: str) -> tuple[float, dict[str, Any]]:
    started = time.perf_counter()
    response = await client.get(
        f"{base_url}/v1/web/search",
        params={"q": query},
        headers=_auth_headers(token),
    )
    elapsed = time.perf_counter() - started
    assert response.status_code == 200, (query, response.status_code, response.text[:500])
    payload = response.json()
    assert "ok" in payload
    assert response.status_code not in {500, 502}
    return elapsed, payload


async def _live_fetch(client: httpx.AsyncClient, base_url: str, token: str, url: str) -> dict[str, Any]:
    response = await client.get(
        f"{base_url}/v1/web/fetch",
        params={"url": url},
        headers=_auth_headers(token),
    )
    assert response.status_code == 200, (response.status_code, response.text[:500])
    payload = response.json()
    assert "ok" in payload
    return payload


def _parse_sse(raw: str) -> tuple[str, bool, list[str], list[str], list[str]]:
    chunks: list[str] = []
    done = False
    events: list[str] = []
    states: list[str] = []
    for block in raw.split("\n\n"):
        event_name = ""
        for line in block.splitlines():
            if line.startswith("event: "):
                event_name = line[7:].strip()
                events.append(event_name)
            if not line.startswith("data: "):
                continue
            data = line[6:].strip()
            if data == "[DONE]":
                done = True
                continue
            with suppress(json.JSONDecodeError):
                payload = json.loads(data)
                state = payload.get("state")
                if isinstance(state, str) and state:
                    states.append(state)
                choices = payload.get("choices")
                if isinstance(choices, list) and choices:
                    delta = choices[0].get("delta") or {}
                    value = delta.get("content") if isinstance(delta, dict) else None
                    if isinstance(value, str) and value:
                        chunks.append(value)
    return "".join(chunks), done, events, chunks, states


async def _live_sse_android_contract(client: httpx.AsyncClient, base_url: str, token: str) -> float:
    headers = _auth_headers(token)
    headers["Content-Type"] = "application/json"
    payload = {
        "messages": [{
            "role": "user",
            "content": "Busca en internet: Python asyncio official documentation.",
        }],
        "personality": "NEUTRO",
    }
    started = time.perf_counter()
    first_text_at: float | None = None
    lines: list[str] = []
    async with client.stream(
        "POST",
        f"{base_url}/v1/chat/stream",
        headers=headers,
        json=payload,
    ) as response:
        assert response.status_code == 200
        async for line in response.aiter_lines():
            lines.append(line)
            if not line.startswith("data: "):
                continue
            data = line[6:].strip()
            if not data or data == "[DONE]":
                continue
            with suppress(json.JSONDecodeError):
                event = json.loads(data)
                choices = event.get("choices")
                if isinstance(choices, list) and choices:
                    delta = choices[0].get("delta") or {}
                    value = delta.get("content") if isinstance(delta, dict) else None
                    if isinstance(value, str) and value and first_text_at is None:
                        first_text_at = time.perf_counter()
    assert first_text_at is not None
    ttft = first_text_at - started
    raw = "\n".join(lines) + "\n"
    answer, done, events, chunks, states = _parse_sse(raw)
    assert done
    assert len(answer.strip()) >= 20
    assert "error" not in events
    assert all(left != right for left, right in zip(chunks, chunks[1:]))
    expected = ["thinking", "searching", "sources_ready", "token_stream"]
    positions = [states.index(state) for state in expected]
    assert positions == sorted(positions)
    return ttft



def test_sse_status_frame_is_android_safe():
    import backend.main as main

    frame = main._sse_status("searching", "gate-status", sources=0)
    raw = frame.decode("utf-8")
    assert raw.startswith("event: status\n")
    payload = json.loads(
        next(line[6:] for line in raw.splitlines() if line.startswith("data: "))
    )
    assert payload == {
        "state": "searching",
        "request_id": "gate-status",
        "sources": 0,
    }
    assert "choices" not in payload


@pytest.mark.skipif(
    not os.getenv("DEEP33_PRODUCTION_GATE_URL"),
    reason="Live production gate requires DEEP33_PRODUCTION_GATE_URL",
)
def test_live_production_gate_zero_5xx_multidomain_and_android_sse():
    require_live = os.getenv("DEEP33_REQUIRE_LIVE_GATE", "").strip().lower()
    assert require_live in {"1", "true", "yes"}, (
        "Live production gate requires DEEP33_REQUIRE_LIVE_GATE=true"
    )
    async def scenario() -> None:
        base_url = os.environ["DEEP33_PRODUCTION_GATE_URL"].rstrip("/")
        token = os.getenv("DEEP33_E2E_TOKEN", "")
        timeout = httpx.Timeout(connect=8.0, read=45.0, write=10.0, pool=5.0)
        latencies: list[float] = []
        domains: set[str] = set()

        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            for query in QUERY_BATTERY:
                elapsed, payload = await _live_search(client, base_url, token, query)
                latencies.append(elapsed)
                for item in payload.get("results") or []:
                    url = str(item.get("url") or "")
                    if "://" in url:
                        domains.add(url.split("://", 1)[1].split("/", 1)[0].removeprefix("www."))
                first = next(
                    (item for item in payload.get("results") or [] if item.get("url")),
                    None,
                )
                if first is not None:
                    await _live_fetch(client, base_url, token, str(first["url"]))

            assert len(domains) >= 3
            assert max(latencies) < 10.0

            stress = await asyncio.gather(
                *[
                    _live_search(
                        client,
                        base_url,
                        token,
                        QUERY_BATTERY[index % len(QUERY_BATTERY)],
                    )
                    for index in range(20)
                ]
            )
            stress_latencies = [elapsed for elapsed, _payload in stress]
            p95 = statistics.quantiles(stress_latencies, n=20, method="inclusive")[18]
            assert p95 < 10.0

            ttft = await _live_sse_android_contract(client, base_url, token)
            assert ttft < 3.0

    asyncio.run(scenario())


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-ra"]))



def test_global_gate_does_not_probe_live_without_explicit_flag(monkeypatch, capsys):
    import importlib.util
    import sys
    from pathlib import Path
    from types import SimpleNamespace

    root_script = Path(__file__).resolve().parents[1] / "test_deep33_global_gate.py"
    spec = importlib.util.spec_from_file_location("_deep33_root_global_gate", root_script)
    assert spec is not None and spec.loader is not None
    gate = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = gate
    spec.loader.exec_module(gate)

    monkeypatch.setattr(
        gate,
        "parse_args",
        lambda: SimpleNamespace(
            base_url="https://deep33-backend.onrender.com",
            token="",
            session_id="unit-test-session",
            require_live=False,
        ),
    )
    monkeypatch.setattr(gate, "run_unit_gate", lambda: True)

    async def unexpected_live_probe(*_args, **_kwargs):
        raise AssertionError("live production probe ran without require_live=true")

    monkeypatch.setattr(gate, "live_gate", unexpected_live_probe)

    assert gate.main() == 0
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["status"] == "PASS"
    assert payload["live"] is False


def test_global_gate_requires_authenticated_token_for_live_mode(monkeypatch, capsys):
    import importlib.util
    import sys
    from pathlib import Path
    from types import SimpleNamespace

    root_script = Path(__file__).resolve().parents[1] / "test_deep33_global_gate.py"
    spec = importlib.util.spec_from_file_location("_deep33_root_global_gate", root_script)
    assert spec is not None and spec.loader is not None
    gate = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = gate
    spec.loader.exec_module(gate)

    monkeypatch.setattr(
        gate,
        "parse_args",
        lambda: SimpleNamespace(
            base_url="https://deep33-backend.onrender.com",
            token="",
            session_id="unit-test-session",
            require_live=True,
        ),
    )
    monkeypatch.setattr(gate, "run_unit_gate", lambda: True)

    async def unexpected_live_probe(*_args, **_kwargs):
        raise AssertionError("live production probe must not run without a token")

    monkeypatch.setattr(gate, "live_gate", unexpected_live_probe)

    assert gate.main() == 1
    assert "DEEP33_TEST_AUTH_TOKEN is empty" in capsys.readouterr().err


def test_web_orchestrator_single_flight_coalesces_concurrent_fresh_requests():
    async def scenario() -> None:
        orchestrator = WebOrchestrator(
            max_results=1,
            provider_total_seconds=0.25,
            global_budget_seconds=0.6,
            fallback_delay_seconds=0,
        )
        calls: dict[str, int] = {}

        def make_provider(name: str):
            async def provider(_query: str, _timeout: float, _limit: int) -> list[dict]:
                calls[name] = calls.get(name, 0) + 1
                await asyncio.sleep(0.03)
                if name in {"google", "bing"}:
                    return [_result(
                        "Python asyncio official documentation",
                        "https://docs.python.org/3/library/asyncio.html",
                        "Official Python asyncio reference documentation for asynchronous I/O.",
                    )]
                return []
            return provider

        for name in list(orchestrator.providers):
            orchestrator.providers[name] = make_provider(name)

        results = await asyncio.gather(*[
            orchestrator.search(
                "Python asyncio official documentation",
                fresh=True,
                budget_seconds=0.5,
            )
            for _ in range(12)
        ])
        assert len(results) == 12
        assert all(isinstance(result.get("results"), list) for result in results)
        # The Google adapter can run in both the primary and recovery wave,
        # but not once per incoming request when single-flight is active.
        assert 0 < calls.get("google", 0) < 12
        baseline = list(results[1]["results"])
        results[0]["results"].append({"title": "single-flight-mutation"})
        assert results[1]["results"] == baseline, (
            "coalesced callers must receive independent payload copies"
        )

    asyncio.run(scenario())


def test_web_orchestrator_honors_fast_budget_even_when_providers_are_slow():
    async def scenario() -> None:
        orchestrator = WebOrchestrator(
            max_results=3,
            provider_total_seconds=0.5,
            global_budget_seconds=0.6,
            fallback_delay_seconds=0,
        )

        async def slow(_query: str, _timeout: float, _limit: int) -> list[dict]:
            await asyncio.sleep(0.5)
            return []

        for name in list(orchestrator.providers):
            orchestrator.providers[name] = slow
        for name in list(orchestrator.fallback_providers):
            orchestrator.fallback_providers[name] = slow

        started = time.perf_counter()
        result = await orchestrator.search(
            "a query with unavailable providers",
            fresh=True,
            budget_seconds=0.15,
        )
        elapsed = time.perf_counter() - started
        assert elapsed < 0.35
        assert result.get("ok") is False

    asyncio.run(scenario())



def test_stream_gateway_emits_visible_fallback_after_provider_timeout(monkeypatch) -> None:
    import backend.main as main

    class TimedOutGateway:
        async def stream(self, *args, **kwargs):
            if False:
                yield b""
            raise main.GatewayTimeoutError()

    monkeypatch.setattr(main, "gateway", TimedOutGateway())

    async def scenario() -> bytes:
        output = bytearray()
        async for chunk in main.stream_gateway(
            {"messages": [{"role": "user", "content": "test"}]},
            "session-timeout",
            "NEUTRO",
            request_id="stream-timeout-fallback-test",
            idempotency_key="stream-timeout-fallback-test",
            request_hash="hash",
            lease_token="lease",
        ):
            output.extend(chunk)
        return bytes(output)

    raw = asyncio.run(scenario()).decode("utf-8", errors="replace")
    assert "No pude completar la síntesis" in raw
    assert "event: error" not in raw
    assert "data: [DONE]" in raw
