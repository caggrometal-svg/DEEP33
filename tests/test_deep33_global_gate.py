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
