from __future__ import annotations

import logging
import os
import socket
import time
import uuid
from datetime import datetime, timezone
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException, Request
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
APP_VERSION = "0.1.3"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("deep33")

NETWORK_CHECK_URL = os.getenv("NETWORK_CHECK_URL", "https://www.google.com/generate_204").strip()
NETWORK_TIMEOUT = float(os.getenv("NETWORK_TIMEOUT_SECONDS", "8"))

gateway = AIGateway()
memory = MemoryClient()
AI_GATEWAY_MODEL = gateway.config.model

app = FastAPI(title=APP_NAME, version=APP_VERSION)


class ChatMessage(BaseModel):
    role: str = Field(pattern="^(system|user|assistant)$")
    content: str = Field(min_length=1, max_length=50000)


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1)
    model: str | None = None
    temperature: float | None = Field(default=None, ge=0, le=2)


class MemoryRememberRequest(BaseModel):
    kind: str = Field(pattern="^(preference|explicit|summary|context)$")
    content: str = Field(min_length=1, max_length=10000)


class MemoryPreferencesRequest(BaseModel):
    personality: str | None = Field(default=None, max_length=32)
    preferences: dict = Field(default_factory=dict)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def session_id_from_request(request: Request) -> str:
    value = request.headers.get("X-DEEP33-Session-Id", "").strip()
    if not value:
        raise HTTPException(status_code=400, detail="DEEP33_SESSION_ID_REQUIRED")
    return value[:128]


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

    latency_ms = round((time.perf_counter() - started) * 1000, 2)
    return {
        "internet_available": bool(dns_ok and https_ok),
        "dns_ok": dns_ok,
        "https_ok": https_ok,
        "latency_ms": latency_ms,
        "timestamp": utc_now(),
        "error": error,
    }


async def gateway_probe() -> dict:
    return await gateway.probe()


@app.get("/health")
async def health() -> dict:
    return {
        "status": "PASS",
        "service": APP_NAME,
        "version": APP_VERSION,
        "timestamp": utc_now(),
    }


@app.get("/ready")
async def ready() -> dict:
    return {"status": "PASS", "ready": True, "timestamp": utc_now()}


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


@app.get("/v1/ai/diagnostics")
async def ai_diagnostics() -> dict:
    network = await network_probe()
    gateway_status = await gateway_probe()
    model_pass = gateway_status["gateway"] == "PASS" and bool(gateway_status.get("model"))

    return {
        "timestamp": utc_now(),
        "checks": {
            "NETWORK": "PASS" if network["internet_available"] else "FAIL",
            "DNS": "PASS" if network["dns_ok"] else "FAIL",
            "HTTPS": "PASS" if network["https_ok"] else "FAIL",
            "BACKEND": "PASS",
            "AI_GATEWAY": gateway_status["gateway"],
            "MODEL": "PASS" if model_pass else "FAIL",
            "CHAT": "NOT_TESTED",
            "MEMORY": "PASS" if memory.enabled else "NOT_CONFIGURED",
        },
        "network": network,
        "gateway": gateway_status,
        "memory": {"enabled": memory.enabled, "backend": "supabase-edge-function" if memory.enabled else None},
        "online": bool(
            network["internet_available"]
            and gateway_status["gateway"] == "PASS"
            and model_pass
        ),
    }


async def call_gateway(payload: dict) -> dict:
    try:
        return await gateway.complete(payload)
    except GatewayTimeoutError as exc:
        raise HTTPException(status_code=504, detail="AI_GATEWAY_TIMEOUT") from exc
    except GatewayHTTPError as exc:
        raise HTTPException(status_code=502, detail="AI_GATEWAY_HTTP_ERROR") from exc
    except GatewayInvalidResponseError as exc:
        raise HTTPException(status_code=502, detail="AI_GATEWAY_INVALID_RESPONSE") from exc


def normalized_generation(data: dict) -> dict:
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
    }


async def prepare_messages(request: ChatRequest, session_id: str) -> list[dict[str, str]]:
    requested = [message.model_dump() for message in request.messages]
    if not memory.enabled:
        return requested[-50:]

    try:
        context = await memory.context(session_id)
        remote = extract_context_messages(context)
        merged = merge_messages(remote, requested, limit=49)
        system_context = extract_context_system_message(context)
        if system_context:
            return [{"role": "system", "content": system_context}, *merged]
        return merged
    except MemoryUnavailableError as exc:
        logger.warning("memory_context_unavailable session_id=%s error=%s", session_id, exc)
        return requested[-50:]


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


async def generate(request: ChatRequest, session_id: str) -> dict:
    request_id = str(uuid.uuid4())
    messages = await prepare_messages(request, session_id)

    payload = {
        "messages": messages,
        "model": request.model or AI_GATEWAY_MODEL,
    }
    if request.temperature is not None:
        payload["temperature"] = request.temperature

    await persist_messages(
        session_id,
        [message for message in messages if message.get("role") != "system"],
    )

    started = time.perf_counter()
    data = await call_gateway(payload)
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    result = normalized_generation(data)

    assistant_message = {"role": "assistant", "content": result["text"]}
    await persist_messages(
        session_id,
        [message for message in messages if message.get("role") != "system"] + [assistant_message],
    )

    logger.info(
        "ai_request request_id=%s session_id=%s provider=%s model=%s latency_ms=%s success=true",
        request_id,
        session_id,
        result.get("provider"),
        result.get("model"),
        elapsed_ms,
    )

    return {
        "request_id": request_id,
        "latency_ms": elapsed_ms,
        "result": result,
        "response": data,
    }


@app.post("/v1/ai/generate")
async def ai_generate(request: ChatRequest, http_request: Request) -> dict:
    return await generate(request, session_id_from_request(http_request))


@app.post("/v1/chat")
async def chat(request: ChatRequest, http_request: Request) -> dict:
    return await generate(request, session_id_from_request(http_request))


@app.get("/v1/memory/context")
async def memory_context(http_request: Request) -> dict:
    if not memory.enabled:
        raise HTTPException(status_code=503, detail="MEMORY_NOT_CONFIGURED")
    session_id = session_id_from_request(http_request)
    try:
        data = await memory.context(session_id)
    except MemoryUnavailableError as exc:
        raise HTTPException(status_code=503, detail="MEMORY_UNAVAILABLE") from exc
    return data


@app.post("/v1/memory/remember")
async def memory_remember(
    payload: MemoryRememberRequest,
    http_request: Request,
) -> dict:
    if not memory.enabled:
        raise HTTPException(status_code=503, detail="MEMORY_NOT_CONFIGURED")
    session_id = session_id_from_request(http_request)
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
    try:
        return await memory.set_preferences(
            session_id,
            personality=payload.personality,
            preferences=payload.preferences,
        )
    except MemoryUnavailableError as exc:
        raise HTTPException(status_code=503, detail="MEMORY_UNAVAILABLE") from exc


async def stream_gateway(payload: dict, session_id: str) -> AsyncIterator[bytes]:
    collected = bytearray()
    async for chunk in gateway.stream(payload):
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
                import json
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
            await persist_messages(session_id, merged + [{"role": "assistant", "content": assistant_text}])
    except MemoryUnavailableError as exc:
        logger.warning("memory_stream_sync_unavailable session_id=%s error=%s", session_id, exc)


@app.post("/v1/chat/stream")
async def chat_stream(request: ChatRequest, http_request: Request) -> StreamingResponse:
    session_id = session_id_from_request(http_request)
    messages = await prepare_messages(request, session_id)
    payload = {
        "messages": messages,
        "model": request.model or AI_GATEWAY_MODEL,
    }
    if request.temperature is not None:
        payload["temperature"] = request.temperature

    await persist_messages(
        session_id,
        [message for message in messages if message.get("role") != "system"],
    )

    return StreamingResponse(
        stream_gateway(payload, session_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
