from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from urllib.parse import urlparse

BASE = os.environ["RAILWAY_BASE_URL"].rstrip("/")
SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = os.environ["SUPABASE_PUBLISHABLE_KEY"]
TOKEN_A = os.environ["DEEP33_RAILWAY_GATE_TOKEN"]
SESSION_A = os.environ["DEEP33_RAILWAY_GATE_SESSION_ID"]
RUN_ID = os.environ["GITHUB_RUN_ID"]
RUN_ATTEMPT = os.environ.get("GITHUB_RUN_ATTEMPT", "1")


def http_json(method: str, url: str, *, token: str | None = None,
              session: str | None = None, body: object | None = None,
              apikey: str | None = None, timeout: float = 20.0,
              request_id: str | None = None,
              idempotency_key: str | None = None):
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    if session:
        headers["X-DEEP33-Session-Id"] = session
    if apikey:
        headers["apikey"] = apikey
    if request_id:
        headers["X-Request-ID"] = request_id
    if idempotency_key:
        headers["X-Idempotency-Key"] = idempotency_key
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body, separators=(",", ":")).encode()
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read()
            status = response.status
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        status = exc.code
    except (urllib.error.URLError, TimeoutError) as exc:
        return 0, {"error": type(exc).__name__, "detail": str(exc)[:180]}, (time.perf_counter() - started) * 1000
    elapsed_ms = (time.perf_counter() - started) * 1000
    try:
        payload = json.loads(raw.decode("utf-8", "replace"))
    except json.JSONDecodeError:
        payload = {"raw": raw.decode("utf-8", "replace")[:800]}
    return status, payload, elapsed_ms


def assert_http(ok: bool, label: str, status: int, payload: dict):
    if not ok:
        safe = {key: payload.get(key) for key in ("ok", "status", "code", "detail", "error", "errors", "engine")}
        raise AssertionError(f"{label} failed: http={status}, detail={json.dumps(safe, ensure_ascii=False)[:600]}")


def get_context(token: str, session: str, label: str):
    status, data, elapsed = http_json(
        "GET", BASE + "/v1/memory/context", token=token,
        session=session, timeout=15,
        request_id=f"railway-memory-read-{RUN_ID}-{label}",
    )
    assert_http(status == 200 and isinstance(data, dict), f"MEMORY_READ_{label}", status, data)
    if not isinstance(data.get("messages"), list):
        raise AssertionError(f"MEMORY_CONTEXT_MESSAGES_MISSING user={label}")
    return data, elapsed


def sync_memory(token: str, session: str, marker: str, label: str):
    body = {
        "messages": [
            {"role": "user", "content": marker},
            {"role": "assistant", "content": f"Persisted by DEEP33 live Railway gate for {label}."},
        ],
        "personality": "NEUTRO",
    }
    status, data, elapsed = http_json(
        "POST", BASE + "/v1/memory/sync", token=token,
        session=session, body=body, timeout=20,
        request_id=f"railway-memory-write-{RUN_ID}-{label}",
        idempotency_key=f"railway-memory-write-{RUN_ID}-{label}",
    )
    assert_http(status == 200 and isinstance(data, dict) and data.get("ok") is True,
                f"MEMORY_WRITE_{label}", status, data)
    print(json.dumps({"memory_write": label, "status": status, "elapsed_ms": round(elapsed, 2)}))


print("RAILWAY_LIVE_MEMORY_WRITE_READ_AB_START")
marker_a = f"RAILWAY_MEMORY_A_{RUN_ID}_{RUN_ATTEMPT}"
status, write_a, write_ms = http_json(
    "POST", BASE + "/v1/memory/sync", token=TOKEN_A, session=SESSION_A,
    body={
        "messages": [
            {"role": "user", "content": marker_a},
            {"role": "assistant", "content": "User A private memory roundtrip."},
        ],
        "personality": "NEUTRO",
    },
    timeout=20, request_id=f"railway-memory-a-{RUN_ID}",
    idempotency_key=f"railway-memory-a-{RUN_ID}",
)
assert_http(status == 200 and isinstance(write_a, dict) and write_a.get("ok") is True,
            "MEMORY_WRITE_A", status, write_a)
ctx_a, read_ms = get_context(TOKEN_A, SESSION_A, "A_AFTER_WRITE")
assert any(item.get("content") == marker_a for item in ctx_a["messages"] if isinstance(item, dict)), \
    "MEMORY_WRITE_READ_ROUNDTRIP_FAILED user=A"

profile_b = f"railway-gate-b-{RUN_ID}-{RUN_ATTEMPT}"
auth_code, auth_data, auth_ms = http_json(
    "POST", SUPABASE_URL + "/functions/v1/deep33-auth",
    body={"action": "session", "memory_profile_id": profile_b},
    apikey=SUPABASE_KEY, timeout=25,
    request_id=f"railway-auth-b-{RUN_ID}",
)
token_b = auth_data.get("access_token") if isinstance(auth_data, dict) else None
assert_http(auth_code == 200 and isinstance(token_b, str) and bool(token_b),
            "AUTH_SESSION_B", auth_code, auth_data)
marker_b = f"RAILWAY_MEMORY_B_{RUN_ID}_{RUN_ATTEMPT}"
sync_memory(token_b, profile_b, marker_b, "B")
ctx_b, read_b_ms = get_context(token_b, profile_b, "B_AFTER_WRITE")
assert any(item.get("content") == marker_b for item in ctx_b["messages"] if isinstance(item, dict)), \
    "MEMORY_WRITE_READ_ROUNDTRIP_FAILED user=B"
ctx_a_final, read_a_final_ms = get_context(TOKEN_A, SESSION_A, "A_ISOLATION_RECHECK")
assert all(item.get("content") != marker_b for item in ctx_a_final["messages"] if isinstance(item, dict)), \
    "MEMORY_USER_ISOLATION_FAILED: user B data visible to user A"
print(json.dumps({
    "memory":"PASS", "write_read_roundtrip":"PASS", "user_isolation":"PASS",
    "user_a_write_ms":round(write_ms,2), "user_a_read_ms":round(read_ms,2),
    "auth_b_ms":round(auth_ms,2), "user_b_read_ms":round(read_b_ms,2),
    "user_a_isolation_recheck_ms":round(read_a_final_ms,2),
}))
print("RAILWAY_MEMORY_WRITE_READ_ISOLATION_AB_PASS")

print("RAILWAY_LIVE_MULTI_CATEGORY_SEARCH_FETCH_START")
queries = [
    ("chile_current_affairs", "latest Chile current affairs news today"),
    ("technology", "latest technology cybersecurity and artificial intelligence news today"),
    ("science", "latest scientific research studies university journal today"),
    ("economy", "latest Chile economy inflation central bank data today"),
    ("sports", "latest sports football results today"),
    ("technical_docs", "latest FastAPI official documentation security authentication"),
    ("international", "latest international news climate policy today"),
]
failed_domains: set[str] = set()
attempted_urls: set[str] = set()
search_times = []
fetch_times = []
total_fetches = 0
for index, (category, query) in enumerate(queries, start=1):
    request_id = f"railway-live-search-{RUN_ID}-{index}"
    url = BASE + "/v1/web/search?" + urllib.parse.urlencode({"q": query})
    status, result, elapsed = http_json(
        "GET", url, token=TOKEN_A, session=SESSION_A, timeout=30,
        request_id=request_id,
    )
    search_times.append(elapsed)
    okay = (
        status == 200 and isinstance(result, dict) and result.get("ok") is True
        and result.get("fresh_request") is True
        and isinstance(result.get("results"), list) and len(result["results"]) >= 3
        and int((result.get("verification") or {}).get("distinct_domains") or 0) >= 3
        and len(result.get("providers_attempted") or []) >= 2
        and isinstance(result.get("retrieved_at"), str) and len(result["retrieved_at"]) > 10
    )
    assert_http(okay, f"LIVE_SEARCH_{category}", status, result if isinstance(result, dict) else {})
    result_items = result["results"]
    urls = [str(item.get("url") or "") for item in result_items if isinstance(item, dict)]
    if len(set(urls)) < 3:
        raise AssertionError(f"LIVE_SEARCH_URL_DEDUP_FAIL category={category}")

    fetched = False
    last_fetch_error = None
    for candidate_index, candidate_url in enumerate(urls, start=1):
        if not candidate_url or candidate_url in attempted_urls:
            continue
        domain = (urlparse(candidate_url).hostname or "").lower().removeprefix("www.")
        if not domain or domain in failed_domains:
            continue
        attempted_urls.add(candidate_url)
        fetch_url = BASE + "/v1/web/fetch?" + urllib.parse.urlencode({"url": candidate_url})
        fetch_code, fetched_data, fetch_ms = http_json(
            "GET", fetch_url, token=TOKEN_A, session=SESSION_A, timeout=10,
            request_id=f"railway-live-fetch-{RUN_ID}-{index}-{candidate_index}",
        )
        total_fetches += 1
        fetch_times.append(fetch_ms)
        valid_fetch = (
            fetch_code == 200 and isinstance(fetched_data, dict)
            and fetched_data.get("ok") is True
            and isinstance(fetched_data.get("text"), str)
            and len(fetched_data["text"].strip()) > 80
            and str(fetched_data.get("final_url") or "").startswith(("http://", "https://"))
        )
        if valid_fetch:
            fetched = True
            break
        failed_domains.add(domain)
        last_fetch_error = {"http": fetch_code, "domain": domain}
    if not fetched:
        raise AssertionError(f"LIVE_FETCH_FAILED category={category}, last_error={last_fetch_error}")
    print(json.dumps({
        "category": category, "search_ms": round(elapsed, 2),
        "result_count": len(result_items),
        "distinct_domains": (result.get("verification") or {}).get("distinct_domains"),
        "providers_attempted": result.get("providers_attempted"),
        "winner_provider": result.get("winner_provider") or result.get("provider"),
        "retrieved_at": result.get("retrieved_at"),
        "fetch":"PASS",
    }, ensure_ascii=False))
print(json.dumps({
    "live_search_fetch":"PASS", "categories":len(queries), "fetches":total_fetches,
    "unique_attempted_urls":len(attempted_urls), "failed_domains_not_retried":len(failed_domains),
    "search_p50_ms":round(sorted(search_times)[len(search_times)//2],2),
    "fetch_p50_ms":round(sorted(fetch_times)[len(fetch_times)//2],2) if fetch_times else None,
}))
print("RAILWAY_LIVE_SEVEN_CATEGORY_SEARCH_FETCH_PASS")

print("RAILWAY_LIVE_WEB_TO_AI_STREAM_START")
stream_payload = {
    "messages": [{
        "role": "user",
        "content": "Busca en la web en tiempo real una noticia científica reciente. Contrasta dos fuentes y responde con hechos comprobables y al menos dos URLs de las fuentes.",
    }],
    "personality": "NEUTRO",
}
stream_key = f"railway-live-stream-{RUN_ID}"
stream_request = urllib.request.Request(
    BASE + "/v1/chat/stream",
    data=json.dumps(stream_payload).encode(),
    headers={
        "Authorization": "Bearer " + TOKEN_A,
        "X-DEEP33-Session-Id": SESSION_A,
        "X-Request-ID": stream_key,
        "X-Idempotency-Key": stream_key,
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "X-DEEP33-Skip-Web-Tools": "false",
    },
    method="POST",
)
started = time.perf_counter()
first_visible_ms = None
source_ready = False
pieces = []
stream_status = 0
try:
    with urllib.request.urlopen(stream_request, timeout=100) as response:
        stream_status = response.status
        for raw in response:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            raw_data = line[5:].strip()
            if raw_data == "[DONE]":
                break
            try:
                event = json.loads(raw_data)
            except json.JSONDecodeError:
                continue
            event_json = json.dumps(event, ensure_ascii=False)
            if "sources_ready" in event_json and int(event.get("sources", 0) or 0) > 0:
                source_ready = True
            choices = event.get("choices") or []
            if choices:
                delta = (choices[0].get("delta") or {}).get("content")
                if isinstance(delta, str) and delta:
                    if first_visible_ms is None:
                        first_visible_ms = (time.perf_counter() - started) * 1000
                    pieces.append(delta)
except urllib.error.HTTPError as exc:
    print(f"RAILWAY_LIVE_STREAM_HTTP_FAIL={exc.code}")
    raise
stream_text = "".join(pieces)
stream_total_ms = (time.perf_counter() - started) * 1000
stream_urls = set(re.findall(r"https?://[^\s)\]}>]+", stream_text))
print(json.dumps({
    "http":stream_status, "source_ready":source_ready,
    "ttft_ms":round(first_visible_ms,2) if first_visible_ms is not None else None,
    "total_ms":round(stream_total_ms,2), "response_chars":len(stream_text),
    "distinct_source_urls_in_response":len(stream_urls),
}))
assert stream_status == 200, "RAILWAY_LIVE_STREAM_HTTP_FAIL"
assert source_ready, "RAILWAY_LIVE_STREAM_NO_WEB_SOURCES"
assert first_visible_ms is not None and first_visible_ms <= 12000, "RAILWAY_TTFT_THRESHOLD_FAIL"
assert stream_total_ms <= 35000, "RAILWAY_TOTAL_LATENCY_THRESHOLD_FAIL"
assert len(stream_text.strip()) >= 80, "RAILWAY_LIVE_STREAM_EMPTY"
assert len(stream_urls) >= 2, "RAILWAY_LIVE_STREAM_SOURCE_EVIDENCE_FAIL"
print("RAILWAY_LIVE_WEB_TO_AI_STREAM_TTFT_PASS")

print("RAILWAY_IDEMPOTENCY_REPLAY_START")
idem_key = f"railway-idempotency-{RUN_ID}"
idem_payload = {
    "messages": [{
        "role":"user",
        "content":"For this idempotency test, respond with the exact token RAILWAY-IDEMPOTENCY-OK and nothing else.",
    }],
    "personality":"NEUTRO",
}
replies = []
latencies = []
for attempt in (1, 2):
    code, data, elapsed = http_json(
        "POST", BASE + "/v1/ai/generate", token=TOKEN_A, session=SESSION_A,
        body=idem_payload, timeout=100, request_id=idem_key,
        idempotency_key=idem_key,
    )
    assert_http(code == 200 and isinstance(data, dict), f"IDEMPOTENCY_ATTEMPT_{attempt}", code, data)
    text = ((data.get("result") or {}).get("text") or "").strip()
    assert text, f"IDEMPOTENCY_EMPTY_RESPONSE attempt={attempt}"
    replies.append(text)
    latencies.append(elapsed)
assert replies[0] == replies[1], "IDEMPOTENCY_RESPONSE_MISMATCH"
print(json.dumps({
    "idempotency":"PASS", "attempts":2, "replay_text_equal":True,
    "attempt_1_ms":round(latencies[0],2), "attempt_2_ms":round(latencies[1],2),
    "response_chars":len(replies[0]),
}))
print("RAILWAY_IDEMPOTENCY_REPLAY_PASS")

print("RAILWAY_METRIC_PERCENTILES_START")
metrics_code, metrics, metrics_ms = http_json(
    "GET", BASE + "/metrics", timeout=15,
    request_id=f"railway-metrics-{RUN_ID}",
)
assert_http(metrics_code == 200 and isinstance(metrics, dict), "METRICS", metrics_code, metrics)
generate_stats = ((metrics.get("endpoints") or {}).get("/v1/ai/generate") or {})
if int(generate_stats.get("count") or 0) < 2:
    raise AssertionError("RAILWAY_GENERATE_METRICS_SAMPLE_COUNT_FAIL")
for percentile in ("p50_ms", "p95_ms", "p99_ms"):
    value = generate_stats.get(percentile)
    if not isinstance(value, (int, float)) or value < 0 or value > 15000:
        raise AssertionError(f"RAILWAY_GENERATE_{percentile.upper()}_THRESHOLD_FAIL value={value}")
print(json.dumps({
    "metrics_read_ms":round(metrics_ms,2),
    "generate":generate_stats,
    "gateway_latency":metrics.get("gateway_latency"),
    "performance_stages":metrics.get("performance"),
}))
print("RAILWAY_GENERATE_P50_P95_P99_PASS")
