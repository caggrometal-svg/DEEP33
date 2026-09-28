from __future__ import annotations

import asyncio
import json
from collections import defaultdict, deque
import logging
import os
from pathlib import Path
import socket
import time
import uuid
from datetime import datetime, timezone
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from backend.gateway import (
    AIGateway,
    GatewayHTTPError,
    GatewayInvalidResponseError,
    GatewayTimeoutError,
)
from backend.memory import (
    MemoryClient,
    MemoryUnavailableError,
    extract_context_messages,
    extract_context_system_message,
    merge_messages,
)

APP_NAME = "DEEP33 Backend"
APP_VERSION = "0.2.0"
def _resolve_git_sha() -> str:
    runtime = os.getenv("RENDER_GIT_COMMIT", "").strip()
    if runtime:
        return runtime
    env_sha = os.getenv("DEEP33_GIT_SHA", "").strip()
    if env_sha:
        return env_sha
    try:
        file_sha = Path("/app/deep33/.deployed_sha").read_text(encoding="utf-8").strip()
        if file_sha:
            return file_sha
    except OSError:
        pass
    return "unknown"

GIT_SHA = _resolve_git_sha()
BUILD_ID = os.getenv("DEEP33_BUILD_ID", "").strip() or f"{APP_VERSION}-{GIT_SHA[:12] or 'unknown'}"
GIT_BRANCH = os.getenv("RENDER_GIT_BRANCH", "main").strip() or "main"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("deep33")

NETWORK_CHECK_URL = os.getenv("NETWORK_CHECK_URL", "https://www.google.com/generate_204").strip()
NETWORK_TIMEOUT = float(os.getenv("NETWORK_TIMEOUT_SECONDS", "8"))
GLOBAL_AI_TIMEOUT = max(10.0, float(os.getenv("AI_TIMEOUT_SECONDS", "75")))
RATE_LIMIT_COUNT = max(1, int(os.getenv("DEEP33_RATE_LIMIT_COUNT", "60")))
RATE_LIMIT_WINDOW = max(10.0, float(os.getenv("DEEP33_RATE_LIMIT_WINDOW_SECONDS", "60")))
CLIENT_AUTH_TOKEN = os.getenv("DEEP33_CLIENT_AUTH_TOKEN", "").strip()

gateway = AIGateway()
memory = MemoryClient()
AI_GATEWAY_MODEL = gateway.config.model

app = FastAPI(title=APP_NAME, version=APP_VERSION)

_metric_counts: dict[str, int] = defaultdict(int)
_metric_latency: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=500))


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((percentile / 100) * (len(ordered) - 1)))))
    return round(ordered[index], 2)


@app.middleware("http")
async def request_metrics(request: Request, call_next):
    started = time.perf_counter()
    incoming = request.headers.get("X-Request-ID", "").strip()
    request_id = incoming[:128] if incoming else str(uuid.uuid4())
    request.state.request_id = request_id
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        elapsed_ms = (time.perf_counter() - started) * 1000
        path = request.url.path
        _metric_counts["requests_total"] += 1
        _metric_counts[f"status_{status}"] += 1
        _metric_latency[path].append(elapsed_ms)
        logger.info(
            "request_metrics request_id=%s method=%s path=%s status=%s latency_ms=%.2f",
            request_id,
            request.method,
            path,
            status,
            elapsed_ms,
        )


@app.get("/metrics")
async def metrics() -> dict:
    endpoints = {}
    for path, values in _metric_latency.items():
        snapshot = list(values)
        endpoints[path] = {
            "count": len(snapshot),
            "p50_ms": _percentile(snapshot, 50),
            "p95_ms": _percentile(snapshot, 95),
            "p99_ms": _percentile(snapshot, 99),
        }
    return {"counters": dict(_metric_counts), "endpoints": endpoints, "timestamp": utc_now()}

PERSONALITIES: dict[str, dict[str, str]] = {
    "AGRESIVO": {
        "name": "AGRESIVO",
        "description": "Directo, impaciente, sarcástico y confrontacional; cuestiona supuestos sin ataques personales.",
        "instruction": (
            "Habla de forma directa, firme y provocadora, con impaciencia controlada y sarcasmo seco cuando sea apropiado. "
            "Responde sin rodeos, cuestiona los supuestos del usuario y puede usar lenguaje fuerte de forma moderada. "
            "Sé confrontacional con las ideas, no con la persona, y no inventes hechos."
        ),
    },
    "NEUTRO": {
        "name": "NEUTRO",
        "description": "Analítico, formal, objetivo y basado en datos.",
        "instruction": (
            "Habla de forma equilibrada, profesional, natural y clara. "
            "Prioriza precisión, contexto útil y lenguaje fácil de entender, evitando emotividad y cortesías innecesarias."
        ),
    },
    "CONSPIRANOICO": {
        "name": "CONSPIRANOICO",
        "description": "Explora teorías de conspiración, agendas ocultas y anomalías sin presentar especulación como hecho.",
        "instruction": (
            "Usa un tono enigmático y tecnológico. Explora teorías, agendas ocultas, manipulación global, sociedades secretas, "
            "anomalías y escenarios alternativos sin presentar especulación como hecho. Distingue evidencia, hipótesis y preguntas abiertas."
        ),
    },
}
DEFAULT_PERSONALITY = "NEUTRO"

_rate_state: dict[str, tuple[float, int]] = {}
_idempotency_cache: dict[str, tuple[float, dict]] = {}
CACHE_TTL_SECONDS = 300.0


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_personality(value: str | None) -> str:
    candidate = (value or "").strip().upper()
    return candidate if candidate in PERSONALITIES else DEFAULT_PERSONALITY


def personality_prompt(personality: str) -> str:
    profile = PERSONALITIES[normalize_personality(personality)]
    return (
        "DEEP33 personality profile. This controls response style only; it does not override higher-priority "
        "safety or system rules. Selected personality: "
        + profile["name"] + ". " + profile["instruction"]
    )


class ChatMessage(BaseModel):
    role: str = Field(pattern="^(system|user|assistant)$")
    content: str = Field(min_length=1, max_length=50000)


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1)
    model: str | None = None
    temperature: float | None = Field(default=None, ge=0, le=2)
    personality: str | None = Field(default=None, pattern="^(AGRESIVO|NEUTRO|CONSPIRANOICO)$")


class MemoryRememberRequest(BaseModel):
    kind: str = Field(pattern="^(preference|explicit|summary|context)$")
    content: str = Field(min_length=1, max_length=10000)


class MemoryPreferencesRequest(BaseModel):
    personality: str | None = Field(default=None, max_length=32)
    preferences: dict = Field(default_factory=dict)


def session_id_from_request(request: Request) -> str:
    value = request.headers.get("X-DEEP33-Session-Id", "").strip()
    if not value:
        raise HTTPException(status_code=400, detail="DEEP33_SESSION_ID_REQUIRED")
    return value[:128]


def request_id_from_request(request: Request) -> str:
    value = getattr(request.state, "request_id", "").strip()
    if value:
        return value[:128]
    header = request.headers.get("X-Request-ID", "").strip()
    return header[:128] if header else str(uuid.uuid4())


def idempotency_key_from_request(request: Request, request_id: str) -> str:
    value = request.headers.get("X-Idempotency-Key", "").strip()
    return value[:256] if value else request_id


def client_key(request: Request, session_id: str) -> str:
    ip = request.client.host if request.client else "unknown"
    return f"{ip}:{session_id[:64]}"


def enforce_client_controls(request: Request, session_id: str) -> None:
    if CLIENT_AUTH_TOKEN:
        supplied = request.headers.get("Authorization", "")
        if supplied != f"Bearer {CLIENT_AUTH_TOKEN}":
            raise HTTPException(status_code=401, detail="DEEP33_CLIENT_AUTH_REQUIRED")

    now = time.monotonic()
    key = client_key(request, session_id)
    window_start, count = _rate_state.get(key, (now, 0))
    if now - window_start >= RATE_LIMIT_WINDOW:
        _rate_state[key] = (now, 1)
        return
    if count >= RATE_LIMIT_COUNT:
        raise HTTPException(status_code=429, detail="DEEP33_RATE_LIMITED")
    _rate_state[key] = (window_start, count + 1)


def cache_key(session_id: str, idempotency_key: str) -> str:
    return f"{session_id}:{idempotency_key}"


def cache_get(session_id: str, idempotency_key: str) -> dict | None:
    key = cache_key(session_id, idempotency_key)
    entry = _idempotency_cache.get(key)
    if entry is None:
        return None
    expires_at, data = entry
    if expires_at <= time.monotonic():
        _idempotency_cache.pop(key, None)
        return None
    return data


def cache_put(session_id: str, idempotency_key: str, data: dict) -> None:
    _idempotency_cache[cache_key(session_id, idempotency_key)] = (
        time.monotonic() + CACHE_TTL_SECONDS,
        data,
    )


async def network_probe() -> dict:
    started = time.perf_counter()
    dns_ok = False
    https_ok = False
    error: str | None = None

    host = NETWORK_CHECK_URL.split("://", 1)[-1].split("/", 1)[0]
    try:
        socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        dns_ok = True
    except OSError as exc:
        error = f"dns:{type(exc).__name__}"

    if dns_ok:
        try:
            import httpx

            async with httpx.AsyncClient(
                timeout=NETWORK_TIMEOUT, follow_redirects=True
            ) as client:
                response = await client.get(NETWORK_CHECK_URL)
                https_ok = 200 <= response.status_code < 400
                if not https_ok:
                    error = f"https:{response.status_code}"
        except Exception as exc:
            error = f"https:{type(exc).__name__}"

    return {
        "internet_available": bool(dns_ok and https_ok),
        "dns_ok": dns_ok,
        "https_ok": https_ok,
        "latency_ms": round((time.perf_counter() - started) * 1000, 2),
        "timestamp": utc_now(),
        "error": error,
    }


async def gateway_probe() -> dict:
    return await gateway.probe()


@app.get("/liveness")
async def liveness() -> dict:
    return {
        "status": "PASS",
        "service": APP_NAME,
        "version": APP_VERSION,
        "git_sha": GIT_SHA,
        "build_id": BUILD_ID,
        "instance_id": os.getenv("RENDER_INSTANCE_ID", "local"),
        "timestamp": utc_now(),
    }


@app.get("/health")
async def health() -> dict:
    return {
        "status": "PASS",
        "service": APP_NAME,
        "version": APP_VERSION,
        "git_sha": GIT_SHA,
        "build_id": BUILD_ID,
        "git_branch": GIT_BRANCH,
        "instance_id": os.getenv("RENDER_INSTANCE_ID", "local"),
        "timestamp": utc_now(),
    }


@app.get("/ready")
async def ready(response: Response) -> dict:
    network = await network_probe()
    gateway_status = await gateway_probe()
    ready_ok = (
        network["internet_available"]
        and gateway_status.get("gateway") == "PASS"
        and bool(gateway_status.get("model"))
        and bool(gateway.config.providers)
    )
    response.status_code = 200 if ready_ok else 503
    return {
        "status": "PASS" if ready_ok else "FAIL",
        "ready": ready_ok,
        "version": APP_VERSION,
        "git_sha": GIT_SHA,
        "build_id": BUILD_ID,
        "timestamp": utc_now(),
        "network": network,
        "gateway": gateway_status,
        "memory_configured": memory.enabled,
    }


@app.get("/v1/network/status")
async def network_status(request: Request) -> dict:
    probe = await network_probe()
    return {
        **probe,
        "backend_reachable": True,
        "backend_host": request.url.hostname,
    }


@app.get("/v1/ai/status")
async def ai_status() -> dict:
    return await gateway_probe()


async def run_inference_check(request_id: str) -> tuple[str, dict | None, str | None]:
    deadline = time.monotonic() + GLOBAL_AI_TIMEOUT
    try:
        data = await asyncio.wait_for(
            gateway.diagnostic_inference(request_id=request_id, deadline=deadline),
            timeout=GLOBAL_AI_TIMEOUT,
        )
        result = normalized_generation(data, DEFAULT_PERSONALITY)
        return "PASS", data, result["text"]
    except GatewayTimeoutError:
        return "TIMEOUT", None, None
    except (GatewayHTTPError, GatewayInvalidResponseError, HTTPException):
        return "FAIL", None, None
    except Exception as exc:
        logger.warning("inference_check_failed request_id=%s error=%s", request_id, type(exc).__name__)
        return "FAIL", None, None


@app.get("/v1/ai/inference-check")
async def inference_check(request: Request) -> dict:
    request_id = request_id_from_request(request)
    status, data, text = await run_inference_check(request_id)
    return {
        "status": status,
        "request_id": request_id,
        "model": data.get("_deep33_gateway", {}).get("model") if isinstance(data, dict) else None,
        "provider": data.get("_deep33_gateway", {}).get("provider") if isinstance(data, dict) else None,
        "text_ok": text == "DEEP33_DIAGNOSTIC_OK",
        "timestamp": utc_now(),
    }


@app.get("/v1/ai/diagnostics")
async def ai_diagnostics(request: Request) -> dict:
    request_id = request_id_from_request(request)
    network = await network_probe()
    gateway_status = await gateway_probe()
    inference_status, inference_data, inference_text = await run_inference_check(request_id)
    model_pass = inference_status == "PASS" and "DEEP33_DIAGNOSTIC_OK" in (inference_text or "")

    return {
        "timestamp": utc_now(),
        "version": APP_VERSION,
        "git_sha": GIT_SHA,
        "build_id": BUILD_ID,
        "request_id": request_id,
        "checks": {
            "NETWORK": "PASS" if network["internet_available"] else "FAIL",
            "DNS": "PASS" if network["dns_ok"] else "FAIL",
            "HTTPS": "PASS" if network["https_ok"] else "FAIL",
            "BACKEND": "PASS",
            "AI_GATEWAY": gateway_status["gateway"],
            "MODEL": "PASS" if model_pass else inference_status,
            "CHAT": "PASS" if model_pass else "FAIL",
            "MEMORY": "PASS" if memory.enabled else "DEGRADED",
        },
        "network": network,
        "gateway": gateway_status,
        "inference": {
            "status": inference_status,
            "provider": (
                inference_data.get("_deep33_gateway", {}).get("provider")
                if isinstance(inference_data, dict)
                else None
            ),
            "model": (
                inference_data.get("_deep33_gateway", {}).get("model")
                if isinstance(inference_data, dict)
                else None
            ),
        },
        "memory": {
            "enabled": memory.enabled,
            "backend": "supabase-edge-function" if memory.enabled else None,
        },
        "online": bool(
            network["internet_available"]
            and gateway_status["gateway"] == "PASS"
            and model_pass
        ),
    }


async def call_gateway(
    payload: dict,
    *,
    request_id: str,
    idempotency_key: str,
) -> dict:
    deadline = time.monotonic() + GLOBAL_AI_TIMEOUT
    try:
        return await asyncio.wait_for(
            gateway.complete(
                payload,
                request_id=request_id,
                idempotency_key=idempotency_key,
                deadline=deadline,
            ),
            timeout=GLOBAL_AI_TIMEOUT,
        )
    except asyncio.TimeoutError as exc:
        raise HTTPException(status_code=504, detail="AI_GATEWAY_TIMEOUT") from exc
    except GatewayTimeoutError as exc:
        raise HTTPException(status_code=504, detail="AI_GATEWAY_TIMEOUT") from exc
    except GatewayHTTPError as exc:
        raise HTTPException(status_code=502, detail="AI_GATEWAY_HTTP_ERROR") from exc
    except GatewayInvalidResponseError as exc:
        raise HTTPException(status_code=502, detail="AI_GATEWAY_INVALID_RESPONSE") from exc


def normalized_generation(data: dict, personality: str = DEFAULT_PERSONALITY) -> dict:
    response = data.get("choices")
    if not isinstance(response, list) or not response:
        raise HTTPException(status_code=502, detail="AI_RESPONSE_CHOICES_MISSING")
    first = response[0]
    if not isinstance(first, dict):
        raise HTTPException(status_code=502, detail="AI_RESPONSE_CHOICE_INVALID")
    message = first.get("message")
    if not isinstance(message, dict):
        raise HTTPException(status_code=502, detail="AI_RESPONSE_MESSAGE_MISSING")
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise HTTPException(status_code=502, detail="AI_RESPONSE_CONTENT_MISSING")

    gateway_meta = data.get("_deep33_gateway", {})
    return {
        "role": "assistant",
        "text": content,
        "model": data.get("model") or gateway_meta.get("model"),
        "provider": gateway_meta.get("provider"),
        "personality": normalize_personality(personality),
    }


async def prepare_messages(request: ChatRequest, session_id: str) -> tuple[list[dict[str, str]], str]:
    requested = [message.model_dump() for message in request.messages]
    selected = normalize_personality(request.personality)
    if not memory.enabled:
        return [{"role": "system", "content": personality_prompt(selected)}, *requested[-49:]], selected

    try:
        context = await memory.context(session_id)
        remote = extract_context_messages(context)
        merged = merge_messages(remote, requested, limit=49)
        system_context = extract_context_system_message(context)
        if system_context:
            return [
                {"role": "system", "content": personality_prompt(selected) + "\n\n" + system_context},
                *merged,
            ], selected
        return [{"role": "system", "content": personality_prompt(selected)}, *merged], selected
    except MemoryUnavailableError as exc:
        logger.warning("memory_context_unavailable session_id=%s error=%s", session_id, exc)
        return [{"role": "system", "content": personality_prompt(selected)}, *requested[-49:]], selected


async def persist_messages(
    session_id: str,
    messages: list[dict[str, str]],
    *,
    personality: str | None = None,
) -> None:
    if not memory.enabled:
        return
    try:
        await memory.sync(session_id, messages, personality=personality)
    except MemoryUnavailableError as exc:
        logger.warning("memory_sync_unavailable session_id=%s error=%s", session_id, exc)


async def generate(
    request: ChatRequest,
    session_id: str,
    *,
    request_id: str,
    idempotency_key: str,
) -> dict:
    cached = cache_get(session_id, idempotency_key)
    if cached is not None:
        logger.info(
            "ai_idempotency_hit request_id=%s session_id=%s",
            request_id,
            session_id,
        )
        return cached

    messages, personality = await prepare_messages(request, session_id)
    payload = {
        "messages": messages,
        "model": request.model or AI_GATEWAY_MODEL,
    }
    if request.temperature is not None:
        payload["temperature"] = request.temperature

    await persist_messages(
        session_id,
        [message for message in messages if message.get("role") != "system"],
        personality=personality,
    )

    started = time.perf_counter()
    data = await call_gateway(
        payload,
        request_id=request_id,
        idempotency_key=idempotency_key,
    )
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    result = normalized_generation(data, personality)

    assistant_message = {"role": "assistant", "content": result["text"]}
    await persist_messages(
        session_id,
        [message for message in messages if message.get("role") != "system"] + [assistant_message],
        personality=personality,
    )

    output = {
        "request_id": request_id,
        "latency_ms": elapsed_ms,
        "result": result,
        "response": data,
    }
    cache_put(session_id, idempotency_key, output)

    logger.info(
        "ai_request request_id=%s session_id=%s provider=%s model=%s latency_ms=%s success=true",
        request_id,
        session_id,
        result.get("provider"),
        result.get("model"),
        elapsed_ms,
    )
    return output


@app.post("/v1/ai/generate")
async def ai_generate(request: ChatRequest, http_request: Request, response: Response) -> dict:
    session_id = session_id_from_request(http_request)
    enforce_client_controls(http_request, session_id)
    request_id = request_id_from_request(http_request)
    idempotency_key = idempotency_key_from_request(http_request, request_id)
    output = await generate(
        request,
        session_id,
        request_id=request_id,
        idempotency_key=idempotency_key,
    )
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Idempotency-Key"] = idempotency_key
    return output


@app.post("/v1/chat")
async def chat(request: ChatRequest, http_request: Request, response: Response) -> dict:
    session_id = session_id_from_request(http_request)
    enforce_client_controls(http_request, session_id)
    request_id = request_id_from_request(http_request)
    idempotency_key = idempotency_key_from_request(http_request, request_id)
    output = await generate(
        request,
        session_id,
        request_id=request_id,
        idempotency_key=idempotency_key,
    )
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Idempotency-Key"] = idempotency_key
    return output


@app.get("/v1/memory/context")
async def memory_context(http_request: Request) -> dict:
    if not memory.enabled:
        raise HTTPException(status_code=503, detail="MEMORY_NOT_CONFIGURED")
    session_id = session_id_from_request(http_request)
    enforce_client_controls(http_request, session_id)
    try:
        return await memory.context(session_id)
    except MemoryUnavailableError as exc:
        raise HTTPException(status_code=503, detail="MEMORY_UNAVAILABLE") from exc


@app.post("/v1/memory/remember")
async def memory_remember(
    payload: MemoryRememberRequest,
    http_request: Request,
) -> dict:
    if not memory.enabled:
        raise HTTPException(status_code=503, detail="MEMORY_NOT_CONFIGURED")
    session_id = session_id_from_request(http_request)
    enforce_client_controls(http_request, session_id)
    try:
        return await memory.remember(session_id, payload.kind, payload.content)
    except MemoryUnavailableError as exc:
        raise HTTPException(status_code=503, detail="MEMORY_UNAVAILABLE") from exc


@app.put("/v1/memory/preferences")
async def memory_preferences(
    payload: MemoryPreferencesRequest,
    http_request: Request,
) -> dict:
    if not memory.enabled:
        raise HTTPException(status_code=503, detail="MEMORY_NOT_CONFIGURED")
    session_id = session_id_from_request(http_request)
    enforce_client_controls(http_request, session_id)
    try:
        return await memory.set_preferences(
            session_id,
            personality=payload.personality,
            preferences=payload.preferences,
        )
    except MemoryUnavailableError as exc:
        raise HTTPException(status_code=503, detail="MEMORY_UNAVAILABLE") from exc


async def stream_gateway(
    payload: dict,
    session_id: str,
    personality: str,
    *,
    request_id: str,
    idempotency_key: str,
) -> AsyncIterator[bytes]:
    collected = bytearray()
    deadline = time.monotonic() + GLOBAL_AI_TIMEOUT
    async for chunk in gateway.stream(
        payload,
        request_id=request_id,
        idempotency_key=idempotency_key,
        deadline=deadline,
    ):
        collected.extend(chunk)
        yield chunk

    try:
        text = collected.decode("utf-8", errors="ignore")
        assistant_parts: list[str] = []
        for line in text.splitlines():
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if not data or data == "[DONE]":
                continue
            try:
                event = json.loads(data)
                choices = event.get("choices")
                if choices and isinstance(choices[0], dict):
                    delta = choices[0].get("delta") or {}
                    content = delta.get("content")
                    if isinstance(content, str):
                        assistant_parts.append(content)
            except Exception:
                continue

        assistant_text = "".join(assistant_parts)
        if assistant_text:
            context = await memory.context(session_id) if memory.enabled else {}
            remote = extract_context_messages(context)
            merged = merge_messages(remote, payload["messages"], limit=50)
            await persist_messages(
                session_id,
                [message for message in merged if message.get("role") != "system"]
                + [{"role": "assistant", "content": assistant_text}],
                personality=personality,
            )
            cache_put(
                session_id,
                idempotency_key,
                {
                    "request_id": request_id,
                    "latency_ms": None,
                    "result": {
                        "role": "assistant",
                        "text": assistant_text,
                        "model": payload.get("model"),
                        "provider": None,
                        "personality": personality,
                    },
                },
            )
    except MemoryUnavailableError as exc:
        logger.warning("memory_stream_sync_unavailable session_id=%s error=%s", session_id, exc)
    except Exception as exc:
        logger.warning(
            "stream_result_parse_failed request_id=%s error=%s",
            request_id,
            type(exc).__name__,
        )


@app.post("/v1/chat/stream")
async def chat_stream(request: ChatRequest, http_request: Request) -> StreamingResponse:
    session_id = session_id_from_request(http_request)
    enforce_client_controls(http_request, session_id)
    request_id = request_id_from_request(http_request)
    idempotency_key = idempotency_key_from_request(http_request, request_id)

    cached = cache_get(session_id, idempotency_key)
    if cached is not None:
        text_value = cached.get("result", {}).get("text", "")
        async def cached_stream() -> AsyncIterator[bytes]:
            yield f'data: {json.dumps({"choices":[{"delta":{"content":text_value}}]})}\n\n'.encode()
            yield b"data: [DONE]\n\n"
        return StreamingResponse(
            cached_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "X-Request-ID": request_id,
            },
        )

    messages, personality = await prepare_messages(request, session_id)
    payload = {
        "messages": messages,
        "model": request.model or AI_GATEWAY_MODEL,
    }
    if request.temperature is not None:
        payload["temperature"] = request.temperature

    await persist_messages(
        session_id,
        [message for message in messages if message.get("role") != "system"],
        personality=personality,
    )

    iterator = stream_gateway(
        payload,
        session_id,
        personality,
        request_id=request_id,
        idempotency_key=idempotency_key,
    )
    try:
        first = await anext(iterator)
    except GatewayTimeoutError as exc:
        raise HTTPException(status_code=504, detail="AI_GATEWAY_TIMEOUT") from exc
    except GatewayHTTPError as exc:
        raise HTTPException(status_code=502, detail="AI_GATEWAY_HTTP_ERROR") from exc
    except GatewayInvalidResponseError as exc:
        raise HTTPException(status_code=502, detail="AI_GATEWAY_INVALID_RESPONSE") from exc

    async def body() -> AsyncIterator[bytes]:
        yield first
        try:
            async for chunk in iterator:
                yield chunk
        except GatewayTimeoutError:
            yield b'event: error\ndata: {"code":"AI_GATEWAY_TIMEOUT"}\n\n'
        except GatewayHTTPError:
            yield b'event: error\ndata: {"code":"AI_GATEWAY_HTTP_ERROR"}\n\n'
        except GatewayInvalidResponseError:
            yield b'event: error\ndata: {"code":"AI_GATEWAY_INVALID_RESPONSE"}\n\n'

    return StreamingResponse(
        body(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "X-Request-ID": request_id,
            "X-Idempotency-Key": idempotency_key,
        },
    )


@app.get("/v1/personalities")
async def personalities() -> dict:
    return {
        "default": DEFAULT_PERSONALITY,
        "personalities": [
            {"name": key, "description": profile["description"]}
            for key, profile in PERSONALITIES.items()
        ],
    }
