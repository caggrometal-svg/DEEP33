#!/usr/bin/env python3
"""Controlled DEEP33 backend comparison: Render vs isolated Railway staging.

Required environment:
  SUPABASE_URL
  SUPABASE_PUBLISHABLE_KEY
  DEEP33_BENCH_RENDER_URL
  DEEP33_BENCH_RAILWAY_URL

Optional:
  DEEP33_BENCH_FAULT_URL       A separate staging-only service with an intentionally
                               invalid AI gateway key. Never point this at production.
  DEEP33_BENCH_SAMPLES=5       Generation and streaming samples per platform.
  DEEP33_BENCH_SEARCH_SAMPLES=3
  DEEP33_BENCH_OUTPUT=benchmark-report.json

No production endpoint/configuration is changed by this script.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from typing import Any


TIMEOUT_SECONDS = 90
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_PUBLISHABLE_KEY", "").strip()
SAMPLES = max(1, min(25, int(os.getenv("DEEP33_BENCH_SAMPLES", "5"))))
SEARCH_SAMPLES = max(1, min(10, int(os.getenv("DEEP33_BENCH_SEARCH_SAMPLES", "3"))))
OUTPUT = os.getenv("DEEP33_BENCH_OUTPUT", "benchmark-report.json")


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    # Nearest-rank percentile, defined consistently for both platforms.
    index = max(0, min(len(ordered) - 1, int((q * len(ordered) + 0.999999)) - 1))
    return round(ordered[index], 2)


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    times = [float(row["latency_ms"]) for row in rows if row.get("latency_ms") is not None]
    statuses: dict[str, int] = {}
    for row in rows:
        status = str(row.get("status", "transport_error"))
        statuses[status] = statuses.get(status, 0) + 1
    return {
        "samples": len(rows),
        "http_2xx": sum(1 for row in rows if isinstance(row.get("status"), int)
                        and 200 <= row["status"] < 300),
        "http_5xx": sum(1 for row in rows if isinstance(row.get("status"), int)
                        and 500 <= row["status"] < 600),
        "statuses": statuses,
        "p50_ms": percentile(times, 0.50),
        "p95_ms": percentile(times, 0.95),
        "first_content_p50_ms": percentile(
            [float(row["first_content_ms"]) for row in rows
             if row.get("first_content_ms") is not None], 0.50),
        "first_content_p95_ms": percentile(
            [float(row["first_content_ms"]) for row in rows
             if row.get("first_content_ms") is not None], 0.95),
        "max_ms": round(max(times), 2) if times else None,
        "raw": rows,
    }


def request(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    payload: dict[str, Any] | None = None,
    stream: bool = False,
) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request_headers = {"User-Agent": "DEEP33-platform-benchmark/1.0"}
    if headers:
        request_headers.update(headers)
    if body is not None:
        request_headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=request_headers, method=method)
    started = time.perf_counter()
    result: dict[str, Any] = {"url_path": urllib.parse.urlsplit(url).path}
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as response:
            result["status"] = response.status
            if stream:
                first_content_ms = None
                first_byte_ms = None
                response_bytes = 0
                while True:
                    line = response.readline()
                    if not line:
                        break
                    response_bytes += len(line)
                    if first_byte_ms is None:
                        first_byte_ms = round((time.perf_counter() - started) * 1000, 2)
                    if not line.startswith(b"data:"):
                        continue
                    data = line[5:].strip()
                    if not data or data == b"[DONE]":
                        continue
                    try:
                        event = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    choices = event.get("choices") or []
                    delta = choices[0].get("delta", {}) if choices else {}
                    if delta.get("content") and first_content_ms is None:
                        first_content_ms = round((time.perf_counter() - started) * 1000, 2)
                result["first_byte_ms"] = first_byte_ms
                result["first_content_ms"] = first_content_ms
                result["response_bytes"] = response_bytes
            else:
                raw = response.read()
                result["response_bytes"] = len(raw)
                try:
                    parsed = json.loads(raw.decode("utf-8"))
                    result["ok"] = parsed.get("ok") if isinstance(parsed, dict) else None
                except (UnicodeDecodeError, json.JSONDecodeError):
                    result["ok"] = None
    except urllib.error.HTTPError as exc:
        result["status"] = exc.code
        try:
            result["error"] = exc.read(2048).decode("utf-8", errors="replace")
        except Exception:
            result["error"] = str(exc)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        result["status"] = "transport_error"
        result["error"] = type(exc).__name__
    result["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
    return result


def make_session(target_name: str) -> tuple[str, str]:
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise RuntimeError("SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY are required")
    profile = f"benchmark-{target_name}-{uuid.uuid4().hex}"
    body = json.dumps({"action": "session", "memory_profile_id": profile}).encode("utf-8")
    req = urllib.request.Request(
        f"{SUPABASE_URL}/functions/v1/deep33-auth",
        data=body,
        headers={"apikey": SUPABASE_KEY, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            if resp.status != 200:
                raise RuntimeError(f"Auth session creation failed (HTTP {resp.status})")
            auth_body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Auth session creation failed (HTTP {exc.code})") from None
    token = auth_body.get("access_token", "")
    if not token:
        raise RuntimeError("Auth endpoint did not return an access_token")
    return token, profile

def auth_headers(token: str, label: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "X-DEEP33-Session-Id": f"benchmark-{label}-{uuid.uuid4().hex}",
        "X-Request-ID": f"benchmark-{uuid.uuid4()}",
    }


def chat_payload(kind: str, run_number: int) -> dict[str, Any]:
    return {
        "messages": [{
            "role": "user",
            "content": (
                f"DEEP33 benchmark {kind} run {run_number}. "
                "Responde brevemente en español con una frase original."
            ),
        }],
        "personality": "NEUTRO",
        "temperature": 0.2,
    }


def benchmark_target(name: str, base_url: str) -> dict[str, Any]:
    base_url = base_url.strip().rstrip("/")
    token, profile = make_session(name)
    report: dict[str, Any] = {
        "target": name,
        "base_url": base_url,
        "auth_profile": profile,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "scenarios": {},
    }

    # First hit after deployment/idle period. This only qualifies as truly cold
    # if the operator has allowed the service to suspend before running the test.
    report["scenarios"]["first_health"] = request("GET", base_url + "/health")
    report["scenarios"]["warm_health"] = summarize([
        request("GET", base_url + "/health") for _ in range(5)
    ])

    search_rows = []
    for index in range(SEARCH_SAMPLES):
        query = f"actualidad de Chile octubre 2026 referencia {uuid.uuid4().hex[:8]}"
        search_rows.append(request(
            "GET",
            base_url + "/v1/web/search?" + urllib.parse.urlencode({"q": query}),
            headers=auth_headers(token, f"{name}-search-{index}"),
        ))
    report["scenarios"]["web_search"] = summarize(search_rows)

    generation_rows = []
    for index in range(SAMPLES):
        generation_rows.append(request(
            "POST",
            base_url + "/v1/ai/generate",
            headers=auth_headers(token, f"{name}-generate-{index}"),
            payload=chat_payload("generate", index),
        ))
    report["scenarios"]["generation"] = summarize(generation_rows)

    stream_rows = []
    for index in range(SAMPLES):
        stream_rows.append(request(
            "POST",
            base_url + "/v1/chat/stream",
            headers={**auth_headers(token, f"{name}-stream-{index}"),
                     "Accept": "text/event-stream"},
            payload=chat_payload("stream", index),
            stream=True,
        ))
    report["scenarios"]["streaming"] = summarize(stream_rows)
    report["ended_at"] = datetime.now(timezone.utc).isoformat()
    return report


def main() -> int:
    targets = {
        "render": os.getenv("DEEP33_BENCH_RENDER_URL", "").strip(),
        "railway": os.getenv("DEEP33_BENCH_RAILWAY_URL", "").strip(),
    }
    missing = [name for name, url in targets.items() if not url]
    if missing:
        print("Missing target URL(s): " + ", ".join(missing), file=sys.stderr)
        print("Set DEEP33_BENCH_RENDER_URL and DEEP33_BENCH_RAILWAY_URL.", file=sys.stderr)
        return 2

    report: dict[str, Any] = {
        "suite": "DEEP33 same-request platform comparison",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "samples": {"generation": SAMPLES, "web_search": SEARCH_SAMPLES, "streaming": SAMPLES},
        "targets": [],
        "notes": [
            "The first health hit is a cold-start measurement only if the target was idle/suspended.",
            "p95 from small sample counts is directional, not a statistically stable SLA.",
            "Run only against an isolated staging service; this suite issues real AI and web-search requests.",
            "Provider fault/recovery testing requires a separate staging URL configured with an intentionally invalid gateway key; it is not sent to production by this script.",
        ],
    }
    for name, url in targets.items():
        print(f"Benchmarking {name}: {url}", flush=True)
        try:
            report["targets"].append(benchmark_target(name, url))
        except Exception as exc:
            report["targets"].append({
                "target": name,
                "fatal_error": f"{type(exc).__name__}: {exc}",
            })

    fault_url = os.getenv("DEEP33_BENCH_FAULT_URL", "").strip().rstrip("/")
    if fault_url:
        # Fault injection must target a separate origin to protect live services.
        fault_host = urllib.parse.urlsplit(fault_url).netloc.lower()
        production_hosts = {
            urllib.parse.urlsplit(url).netloc.lower()
            for url in targets.values() if url
        }
        if not fault_host or fault_host in production_hosts:
            raise RuntimeError(
                "DEEP33_BENCH_FAULT_URL must be a separate staging origin, "
                "not either comparison target"
            )
        # Configure this isolated instance with an intentionally invalid AI_GATEWAY_API_KEY.
        token, profile = make_session("railway-fault")
        failed = request(
            "POST",
            fault_url + "/v1/ai/generate",
            headers=auth_headers(token, "fault-injection"),
            payload=chat_payload("provider-fault-injection", 1),
        )
        token_after, _ = make_session("railway-recovery")
        recovered = request(
            "POST",
            targets["railway"] + "/v1/ai/generate",
            headers=auth_headers(token_after, "post-fault-recovery"),
            payload=chat_payload("recovery", 1),
        )
        report["provider_failure_and_recovery"] = {
            "fault_target_status": failed.get("status"),
            "fault_target_latency_ms": failed.get("latency_ms"),
            "recovery_target_status": recovered.get("status"),
            "recovery_target_latency_ms": recovered.get("latency_ms"),
            "auth_profile": profile,
            "fault_injection_url": fault_url,
        }

    with open(OUTPUT, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(json.dumps({
        "output": OUTPUT,
        "target_summaries": [{
            "target": target.get("target"),
            "fatal_error": target.get("fatal_error"),
            "scenarios": {
                name: {key: value for key, value in scenario.items() if key != "raw"}
                for name, scenario in target.get("scenarios", {}).items()
                if isinstance(scenario, dict)
            },
        } for target in report["targets"]],
        "provider_failure_and_recovery": report.get("provider_failure_and_recovery"),
    }, ensure_ascii=False, indent=2))
    return 0 if all("fatal_error" not in t for t in report["targets"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
