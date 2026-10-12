from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from urllib.parse import urlparse

import httpx

from backend.search.resilient import WebOrchestrator
from backend.search.web_pipeline import FetchOutcome, fetch_many, rank_evidence

ROOT = Path(__file__).resolve().parent


def _result(title: str, url: str, snippet: str) -> dict:
    return {"title": title, "url": url, "snippet": snippet}


class GlobalGateTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.loop_errors: list[dict] = []
        self.loop = asyncio.get_running_loop()
        self.previous_handler = self.loop.get_exception_handler()

        def handler(_loop: asyncio.AbstractEventLoop, context: dict) -> None:
            self.loop_errors.append(dict(context))

        self.loop.set_exception_handler(handler)

    async def asyncTearDown(self) -> None:
        await asyncio.sleep(0)
        self.loop.set_exception_handler(self.previous_handler)
        self.assertEqual(
            self.loop_errors,
            [],
            f"Unretrieved async exception(s): {self.loop_errors}",
        )

    async def test_search_fallback_bing_mojeek_offline_duckduckgo_wins(self) -> None:
        orchestrator = WebOrchestrator(
            max_results=3,
            provider_total_seconds=0.08,
            global_budget_seconds=0.40,
            fallback_delay_seconds=0.02,
            cache_ttl_seconds=30,
        )

        async def offline(_query: str, _timeout: float, _limit: int) -> list[dict]:
            await asyncio.sleep(0.2)
            return []

        async def ddg(_query: str, _timeout: float, _limit: int) -> list[dict]:
            await asyncio.sleep(0.01)
            return [
                _result("Python asyncio TaskGroup", "https://docs.python.org/3/library/asyncio-task.html", "asyncio task groups and cancellation"),
                _result("Async IO guide", "https://realpython.com/async-io-python/", "Python asynchronous IO guide"),
                _result("PEP 654", "https://peps.python.org/pep-0654/", "exception groups and async structured concurrency"),
            ]

        orchestrator.providers["bing"] = offline
        orchestrator.providers["mojeek"] = offline
        orchestrator.providers["duckduckgo"] = ddg

        result = await orchestrator.search("Python asyncio structured concurrency", fresh=True)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["status"], "OK")
        self.assertEqual(result["winner_provider"], "duckduckgo")
        self.assertNotIn("bing", result["providers"])
        self.assertNotIn("mojeek", result["providers"])
        self.assertLess(result["latency_ms"], 400.0)

    async def test_general_provider_query_compacts_search_noise_but_preserves_ranking_query(self) -> None:
        orchestrator = WebOrchestrator(
            max_results=3,
            provider_total_seconds=0.08,
            global_budget_seconds=0.30,
            fallback_delay_seconds=0.01,
        )
        seen: list[str] = []

        async def provider(query: str, _timeout: float, _limit: int) -> list[dict]:
            seen.append(query)
            return [
                _result(
                    "asyncio — Asynchronous I/O",
                    "https://docs.python.org/3/library/asyncio.html",
                    "Python asyncio documentation and event loop APIs",
                ),
                _result(
                    "Python asyncio tutorial",
                    "https://realpython.com/async-io-python/",
                    "Python asyncio guide",
                ),
                _result(
                    "PEP 3156",
                    "https://peps.python.org/pep-3156/",
                    "Python asyncio event loop design",
                ),
            ]

        orchestrator.providers["bing"] = provider
        orchestrator.providers["mojeek"] = provider
        orchestrator.providers["duckduckgo"] = provider
        original = "Python asyncio official documentation"
        result = await orchestrator.search(original, fresh=True)

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["query"], original)
        self.assertEqual(result["queries_executed"], ["python asyncio official"])
        self.assertTrue(seen)
        self.assertTrue(all(query == "python asyncio official" for query in seen))

    async def test_circuit_breaker_opens_after_five_consecutive_failures(self) -> None:
        orchestrator = WebOrchestrator(
            provider_total_seconds=0.05,
            global_budget_seconds=0.20,
            fallback_delay_seconds=0.0,
        )

        async def fail(_query: str, _timeout: float, _limit: int) -> list[dict]:
            raise RuntimeError("provider offline")

        orchestrator.providers["bing"] = fail
        for _ in range(5):
            outcome = await orchestrator._call_provider("bing", "test")
            self.assertFalse(outcome.ok)

        sixth = await orchestrator._call_provider("bing", "test")
        self.assertFalse(sixth.ok)
        self.assertEqual(sixth.error_code, "CIRCUIT_OPEN")

    async def test_partial_fetch_failures_do_not_abort_successful_sources(self) -> None:
        outcomes = {
            "https://ok.example/a": FetchOutcome(
                url="https://ok.example/a",
                ok=True,
                status="OK",
                document={"ok": True, "url": "https://ok.example/a", "text": "valid evidence A"},
                latency_ms=10,
            ),
            "https://blocked.example/b": FetchOutcome(
                url="https://blocked.example/b",
                ok=False,
                status="WEB_FETCH_HTTP_403",
                document=None,
                latency_ms=12,
            ),
            "https://broken.example/c": FetchOutcome(
                url="https://broken.example/c",
                ok=False,
                status="WEB_FETCH_HTTP_500",
                document=None,
                latency_ms=13,
            ),
            "https://slow.example/d": FetchOutcome(
                url="https://slow.example/d",
                ok=False,
                status="FETCH_TIMEOUT",
                document=None,
                latency_ms=2500,
            ),
            "https://ok.example/e": FetchOutcome(
                url="https://ok.example/e",
                ok=True,
                status="OK",
                document={"ok": True, "url": "https://ok.example/e", "text": "valid evidence E"},
                latency_ms=11,
            ),
        }

        async def fake_fetch_one(url: str, **_kwargs) -> FetchOutcome:
            await asyncio.sleep(0)
            return outcomes[url]

        with patch("backend.search.web_pipeline.fetch_one", side_effect=fake_fetch_one):
            result = await fetch_many(list(outcomes), concurrency=4)

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["fetched"], 2)
        self.assertEqual(result["failed"], 3)
        statuses = {item["status"] for item in result["failures"]}
        self.assertEqual(
            statuses,
            {"WEB_FETCH_HTTP_403", "WEB_FETCH_HTTP_500", "FETCH_TIMEOUT"},
        )

    async def test_multidomain_ranking_formula_orders_relevant_evidence(self) -> None:
        query = "clima actual Santiago Chile"
        ranked = rank_evidence(
            query,
            [
                {
                    "title": "Pronóstico actual Santiago",
                    "url": "https://www.meteochile.gob.cl/PortalDMC-web/index.xhtml",
                    "snippet": "Clima y temperatura actual en Santiago Chile",
                    "published_at": "2026-10-12T05:00:00+00:00",
                },
                {
                    "title": "Receta de pan",
                    "url": "https://example.com/pan",
                    "snippet": "Harina agua levadura",
                    "published_at": "2026-10-12T05:00:00+00:00",
                },
            ],
        )
        self.assertGreater(ranked[0]["score"], ranked[1]["score"])
        self.assertIn("meteochile.gob.cl", ranked[0]["url"])

    async def test_realtime_intent_contract(self) -> None:
        from backend import main

        realtime = [
            "¿Qué pasó hoy en Chile?",
            "Dame las últimas noticias de política Chile",
            "¿Cuál es el precio actual del dólar?",
            "¿Cómo está el clima hoy en Santiago?",
            "¿A qué hora es el partido de Chile?",
        ]
        for query in realtime:
            self.assertTrue(
                main.should_force_web([{"role": "user", "content": query}]),
                query,
            )
        self.assertFalse(
            main.should_force_web([{"role": "user", "content": "¿Cuánto es 7 por 8?"}])
        )

    async def test_android_sse_state_order_and_no_empty_fallback_chunks(self) -> None:
        from backend import main

        source = (ROOT / "backend" / "main.py").read_text(encoding="utf-8")
        route_start = source.index('@app.post("/v1/chat/stream")')
        route_end = source.index("def _sse_text_chunks", route_start)
        route = source[route_start:route_end]
        deferred_start = route.index("async def deferred_stream")
        deferred = route[deferred_start:]

        ordered = [
            'yield _sse_status("thinking"',
            'yield _sse_status("searching"',
            '"sources_ready"',
            'yield _sse_status("token_stream"',
        ]
        offsets = [deferred.index(marker) for marker in ordered]
        self.assertEqual(offsets, sorted(offsets))
        self.assertIn("await asyncio.gather(*pending_tasks, return_exceptions=True)", deferred)
        self.assertEqual(main._sse_text_chunks(""), [])
        payload = main._sse_delta("x").decode("utf-8")
        decoded = json.loads(payload.split("data: ", 1)[1])
        self.assertEqual(decoded["choices"][0]["delta"]["content"], "x")

    async def test_web_endpoints_do_not_raise_502_for_provider_or_source_failures(self) -> None:
        source = (ROOT / "backend" / "main.py").read_text(encoding="utf-8")
        start = source.index('@app.get("/v1/web/search")')
        end = source.index("async def run_inference_check", start)
        web_routes = source[start:end]
        self.assertNotIn('status_code=502', web_routes)
        self.assertIn("graceful_payload(", web_routes)
        self.assertIn("DiagnosticCode.PROVIDER_TIMEOUT", web_routes)
        self.assertIn("DiagnosticCode.FETCH_TIMEOUT", web_routes)

    async def test_no_async_task_exception_leak_in_search_chaos(self) -> None:
        orchestrator = WebOrchestrator(
            provider_total_seconds=0.04,
            global_budget_seconds=0.15,
            fallback_delay_seconds=0.01,
        )

        async def explode(_query: str, _timeout: float, _limit: int) -> list[dict]:
            await asyncio.sleep(0)
            raise ValueError("chaos")

        orchestrator.providers["bing"] = explode
        orchestrator.providers["mojeek"] = explode
        orchestrator.providers["duckduckgo"] = explode
        result = await orchestrator.search("política Chile hoy", fresh=True)
        self.assertFalse(result["ok"])
        self.assertIn(result["status"], {"NO_RESULTS", "PROVIDER_TIMEOUT"})
        await asyncio.sleep(0.02)


async def live_gate(base_url: str, token: str | None, session_id: str) -> None:
    base = base_url.rstrip("/")
    headers = {
        "X-Request-ID": f"deep33-global-gate-{int(time.time())}",
        "X-Session-ID": session_id,
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    def percentile(values: list[float], q: float) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        index = max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * q))))
        return ordered[index]

    def duplicated_complete_response(text: str) -> bool:
        normalized = " ".join((text or "").split()).strip()
        if not normalized:
            return False
        size = len(normalized)
        if size % 2 == 0 and normalized[: size // 2] == normalized[size // 2 :]:
            return True
        markers = [
            "El servicio de DEEP33 está temporalmente fuera de servicio",
            "Servicio temporalmente no disponible",
        ]
        return any(normalized.count(marker) > 1 for marker in markers)

    async with httpx.AsyncClient(timeout=httpx.Timeout(12.0, connect=3.0), follow_redirects=True) as client:
        topics = [
            "Python asyncio TaskGroup documentación oficial",
            "política Chile hoy últimas noticias",
            "clima actual Santiago Chile",
            "noticias económicas Chile hoy",
            "fútbol internacional resultados hoy",
            "medicina general influenza información oficial",
            "producto tecnológico smartphone actual especificaciones",
            "universidad investigación inteligencia artificial paper",
            "Wikipedia asyncio Python",
            "sitio gubernamental Chile información pública",
            "sitio comercial tecnología Chile",
            "medio internacional noticias mundo hoy",
        ]

        multidomain_evidence: list[dict] = []
        for query in topics:
            started = time.perf_counter()
            response = await client.get(
                f"{base}/v1/web/search",
                params={"q": query},
                headers=headers,
            )
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            if response.status_code == 502:
                raise AssertionError(f"502 forbidden for query: {query}")
            if response.status_code != 200:
                raise AssertionError(
                    f"Expected HTTP 200 for web search, got {response.status_code}: {response.text[:300]}"
                )
            payload = response.json()
            diagnostic = payload.get("diagnostic_code") or payload.get("code")
            if "ok" not in payload or (not payload.get("ok") and not diagnostic):
                raise AssertionError(f"Invalid Android-safe web payload: {payload}")
            if not payload.get("ok"):
                raise AssertionError(f"Live multidomain query returned no usable web evidence: {query}: {payload}")

            results = payload.get("results") or payload.get("sources") or []
            domains: list[str] = []
            urls: list[str] = []
            for item in results:
                url = str(item.get("url") or "").strip()
                host = (urlparse(url).hostname or "").casefold()
                if url and host and host not in domains:
                    domains.append(host)
                    urls.append(url)
            if len(domains) < 3:
                raise AssertionError(
                    f"Multidomain gate failed for {query!r}: expected >=3 domains, got {domains}"
                )

            fetch_ok = 0
            fetch_fail = 0
            for url in urls[:3]:
                fetched = await client.get(
                    f"{base}/v1/web/fetch",
                    params={"url": url},
                    headers=headers,
                )
                if fetched.status_code == 502:
                    raise AssertionError(f"502 forbidden for source fetch: {url}")
                if fetched.status_code >= 500:
                    fetch_fail += 1
                    continue
                body = fetched.json()
                if body.get("ok"):
                    fetch_ok += 1
                else:
                    fetch_fail += 1
            if fetch_ok < 2:
                raise AssertionError(
                    f"Real navigation gate failed for {query!r}: fetch_success={fetch_ok}/3"
                )
            multidomain_evidence.append(
                {
                    "query": query,
                    "domains": domains[:5],
                    "search_ms": round(elapsed_ms, 2),
                    "fetch_success": fetch_ok,
                    "fetch_failed": fetch_fail,
                }
            )

        # Live source-chaos: partial source failures must stay Android-safe and never become 502.
        chaos_urls = [
            "https://httpstat.us/403",
            "https://httpstat.us/500",
            "https://docs.python.org/3/library/asyncio.html",
        ]
        chaos_status: list[dict] = []
        for url in chaos_urls:
            resp = await client.get(f"{base}/v1/web/fetch", params={"url": url}, headers=headers)
            if resp.status_code == 502:
                raise AssertionError(f"Live source chaos exposed avoidable 502 for {url}")
            chaos_status.append({"url": url, "http": resp.status_code})

        async def run_stream(prompt: str, suffix: str) -> dict:
            chat_payload = {
                "messages": [{"role": "user", "content": prompt}],
                "personality": "NEUTRO",
            }
            stream_headers = {
                **headers,
                "Accept": "text/event-stream",
                "Idempotency-Key": f"gate-{suffix}-{int(time.time() * 1000)}",
            }
            started = time.perf_counter()
            first_token_at: float | None = None
            statuses: list[str] = []
            deltas: list[str] = []

            async with client.stream(
                "POST",
                f"{base}/v1/chat/stream",
                json=chat_payload,
                headers=stream_headers,
            ) as response:
                if response.status_code == 502:
                    raise AssertionError("502 forbidden for Android SSE")
                if response.status_code != 200:
                    body = (await response.aread()).decode("utf-8", "replace")
                    raise AssertionError(
                        f"SSE expected HTTP 200, got {response.status_code}: {body[:400]}"
                    )
                event = ""
                async for line in response.aiter_lines():
                    if line.startswith("event: "):
                        event = line[7:].strip()
                        continue
                    if not line.startswith("data: "):
                        continue
                    data = line[6:]
                    if data == "[DONE]":
                        break
                    if event == "status":
                        status_payload = json.loads(data)
                        statuses.append(status_payload["state"])
                        event = ""
                        continue
                    payload = json.loads(data)
                    delta = (
                        payload.get("choices", [{}])[0]
                        .get("delta", {})
                        .get("content", "")
                    )
                    if delta:
                        if first_token_at is None:
                            first_token_at = time.perf_counter()
                        deltas.append(delta)

            if first_token_at is None:
                raise AssertionError(f"SSE returned no visible token for {prompt!r}")
            required_order = ["thinking", "searching", "sources_ready", "token_stream"]
            cursor = -1
            for state in required_order:
                try:
                    cursor = statuses.index(state, cursor + 1)
                except ValueError as exc:
                    raise AssertionError(
                        f"Missing SSE state {state}; got {statuses}; prompt={prompt!r}"
                    ) from exc

            rebuilt = "".join(deltas).strip()
            if not rebuilt:
                raise AssertionError("Empty final SSE response")
            if duplicated_complete_response(rebuilt):
                raise AssertionError(f"Duplicated complete SSE response detected: {rebuilt[:240]}")

            ttft = first_token_at - started
            total = time.perf_counter() - started
            if ttft >= 3.0:
                raise AssertionError(f"TTFT gate failed: {ttft:.3f}s >= 3.0s")
            if total >= 10.0:
                raise AssertionError(f"Total latency gate failed: {total:.3f}s >= 10.0s")
            return {
                "prompt": prompt,
                "ttft_ms": round(ttft * 1000, 2),
                "total_ms": round(total * 1000, 2),
                "states": statuses,
                "chars": len(rebuilt),
            }

        stream_cases = [
            "¿Cuál es el presidente actual de Chile?",
            "Busca las últimas noticias sobre Chile hoy.",
            "¿Qué dice la documentación oficial de asyncio?",
            "Compara información actual de tres sitios sobre inteligencia artificial.",
            "Busca información en Internet sobre el clima actual de Santiago.",
        ]
        stream_results = []
        for index, prompt in enumerate(stream_cases):
            stream_results.append(await run_stream(prompt, str(index)))

        async def one_search(i: int) -> float:
            started = time.perf_counter()
            resp = await client.get(
                f"{base}/v1/web/search",
                params={"q": f"Python asyncio documentación oficial concurrencia {i}"},
                headers={**headers, "X-Request-ID": f"stress-{i}-{int(time.time()*1000)}"},
            )
            elapsed = time.perf_counter() - started
            if resp.status_code >= 500:
                raise AssertionError(f"Stress request {i} failed with HTTP {resp.status_code}")
            if resp.status_code != 200:
                raise AssertionError(f"Stress request {i} unexpected HTTP {resp.status_code}")
            return elapsed

        stress_results: list[dict] = []
        for concurrency in (1, 5, 10, 20):
            latencies = await asyncio.gather(*(one_search(i) for i in range(concurrency)))
            ms = [value * 1000.0 for value in latencies]
            stress_results.append(
                {
                    "concurrency": concurrency,
                    "p50_ms": round(percentile(ms, 0.50), 2),
                    "p95_ms": round(percentile(ms, 0.95), 2),
                    "p99_ms": round(percentile(ms, 0.99), 2),
                    "max_ms": round(max(ms), 2),
                    "errors": 0,
                }
            )

        print(
            json.dumps(
                {
                    "gate": "DEEP33-TEST-GLOBAL-101026",
                    "live": True,
                    "status": "PASS",
                    "multidomain": multidomain_evidence,
                    "source_chaos": chaos_status,
                    "stream": stream_results,
                    "stress": stress_results,
                },
                ensure_ascii=False,
            )
        )

def run_unit_gate() -> bool:
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(GlobalGateTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return result.wasSuccessful()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="DEEP33-TEST-GLOBAL-101026 production resilience gate"
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("DEEP33_BASE_URL", ""),
        help="Live DEEP33 base URL. When supplied, Android SSE TTFT and HTTP contract are validated.",
    )
    parser.add_argument(
        "--token",
        default=os.getenv("DEEP33_TEST_AUTH_TOKEN", ""),
        help="Optional bearer token for live endpoints.",
    )
    parser.add_argument(
        "--session-id",
        default=os.getenv("DEEP33_TEST_SESSION_ID", "deep33-production-gate"),
    )
    parser.add_argument(
        "--require-live",
        action="store_true",
        default=os.getenv("DEEP33_REQUIRE_LIVE_GATE", "").lower() in {"1", "true", "yes"},
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not run_unit_gate():
        return 1

    if args.base_url:
        try:
            asyncio.run(live_gate(args.base_url, args.token or None, args.session_id))
        except Exception as exc:
            print(
                json.dumps(
                    {
                        "gate": "DEEP33-TEST-GLOBAL-101026",
                        "live": True,
                        "status": "FAIL",
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                    ensure_ascii=False,
                ),
                file=sys.stderr,
            )
            return 1
    elif args.require_live:
        print(
            "DEEP33_REQUIRE_LIVE_GATE is enabled but DEEP33_BASE_URL is empty",
            file=sys.stderr,
        )
        return 1

    print(
        json.dumps(
            {
                "gate": "DEEP33-TEST-GLOBAL-101026",
                "unit": True,
                "live": bool(args.base_url),
                "status": "PASS",
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
