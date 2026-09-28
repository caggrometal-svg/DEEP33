from __future__ import annotations

import os
import socket
import time
import uuid
from datetime import datetime, timezone
from typing import AsyncIterator

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

APP_NAME = "DEEP33 Backend"
APP_VERSION = "0.1.1"

# Connectivity is part of the backend core. The provider remains replaceable,
# but DEEP33 boots with a keyless free gateway so the backbone can be proven
# before adding any paid or private provider.
AI_GATEWAY_URL = os.getenv(
    "AI_GATEWAY_URL",
    "https://api.kilo.ai/api/gateway/chat/completions",
).strip()
AI_GATEWAY_API_KEY = os.getenv("AI_GATEWAY_API_KEY", "").strip()
AI_GATEWAY_MODEL = os.getenv("AI_GATEWAY_MODEL", "kilo-auto/free").strip()
AI_GATEWAY_HEALTH_URL = os.getenv(
    "AI_GATEWAY_HEALTH_URL",
    "https://api.kilo.ai/api/gateway/models",
).strip()

NETWORK_CHECK_URL = os.getenv(
    "NETWORK_CHECK_URL", "https://www.google.com/generate_204"
).strip()
NETWORK_TIMEOUT = float(os.getenv("NETWORK_TIMEOUT_SECONDS", "8"))
AI_TIMEOUT = float(os.getenv("AI_TIMEOUT_SECONDS", "45"))

app = FastAPI(title=APP_NAME, version=APP_VERSION)


class ChatMessage(BaseModel):
    role: str = Field(pattern="^(system|user|assistant)$")
    content: str = Field(min_length=1, max_length=50000)


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1)
    model: str | None = None
    temperature: float | None = Field(default=None, ge=0, le=2)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
            async with httpx.AsyncClient(
                timeout=NETWORK_TIMEOUT, follow_redirects=True
            ) as client:
                response = await client.get(NETWORK_CHECK_URL)
                https_ok = 200 <= response.status_code < 400
                if not https_ok:
                    error = f"https:{response.status_code}"
        except httpx.HTTPError as exc:
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
    if not AI_GATEWAY_HEALTH_URL:
        return {
            "gateway": "NOT_VERIFIED",
            "provider": None,
            "model": AI_GATEWAY_MODEL or None,
            "latency_ms": None,
            "last_success": None,
            "last_error": "AI_GATEWAY_HEALTH_URL is not configured",
        }

    started = time.perf_counter()
    headers = (
        {"Authorization": f"Bearer {AI_GATEWAY_API_KEY}"}
        if AI_GATEWAY_API_KEY
        else {}
    )

    try:
        async with httpx.AsyncClient(timeout=AI_TIMEOUT) as client:
            response = await client.get(AI_GATEWAY_HEALTH_URL, headers=headers)
            latency_ms = round((time.perf_counter() - started) * 1000, 2)
            passed = 200 <= response.status_code < 300
            return {
                "gateway": "PASS" if passed else "FAIL",
                "provider": response.headers.get("x-provider"),
                "model": AI_GATEWAY_MODEL or None,
                "latency_ms": latency_ms,
                "last_success": utc_now() if passed else None,
                "last_error": None if passed else f"http:{response.status_code}",
            }
    except httpx.TimeoutException:
        return {
            "gateway": "TIMEOUT",
            "provider": None,
            "model": AI_GATEWAY_MODEL or None,
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            "last_success": None,
            "last_error": "timeout",
        }
    except httpx.HTTPError as exc:
        return {
            "gateway": "FAIL",
            "provider": None,
            "model": AI_GATEWAY_MODEL or None,
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            "last_success": None,
            "last_error": type(exc).__name__,
        }


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
    gateway = await gateway_probe()
    model_pass = gateway["gateway"] == "PASS" and bool(gateway.get("model"))

    return {
        "timestamp": utc_now(),
        "checks": {
            "NETWORK": "PASS" if network["internet_available"] else "FAIL",
            "DNS": "PASS" if network["dns_ok"] else "FAIL",
            "HTTPS": "PASS" if network["https_ok"] else "FAIL",
            "BACKEND": "PASS",
            "AI_GATEWAY": gateway["gateway"],
            "MODEL": "PASS" if model_pass else "FAIL",
            "CHAT": "NOT_TESTED",
        },
        "network": network,
        "gateway": gateway,
        "online": bool(
            network["internet_available"]
            and gateway["gateway"] == "PASS"
            and model_pass
        ),
    }


async def call_gateway(payload: dict) -> dict:
    headers = {"Content-Type": "application/json"}
    if AI_GATEWAY_API_KEY:
        headers["Authorization"] = f"Bearer {AI_GATEWAY_API_KEY}"

    try:
        async with httpx.AsyncClient(timeout=AI_TIMEOUT) as client:
            response = await client.post(
                AI_GATEWAY_URL,
                json=payload,
                headers=headers,
            )
            response.raise_for_status()
            return response.json()
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail="AI_GATEWAY_TIMEOUT") from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail="AI_GATEWAY_HTTP_ERROR") from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(
            status_code=502, detail="AI_GATEWAY_INVALID_RESPONSE"
        ) from exc


@app.post("/v1/chat")
async def chat(request: ChatRequest) -> dict:
    request_id = str(uuid.uuid4())
    payload = {
        "messages": [message.model_dump() for message in request.messages],
        "model": request.model or AI_GATEWAY_MODEL,
    }
    if request.temperature is not None:
        payload["temperature"] = request.temperature

    started = time.perf_counter()
    data = await call_gateway(payload)
    return {
        "request_id": request_id,
        "latency_ms": round((time.perf_counter() - started) * 1000, 2),
        "response": data,
    }


async def stream_gateway(payload: dict) -> AsyncIterator[bytes]:
    headers = {"Content-Type": "application/json"}
    if AI_GATEWAY_API_KEY:
        headers["Authorization"] = f"Bearer {AI_GATEWAY_API_KEY}"

    async with httpx.AsyncClient(timeout=AI_TIMEOUT) as client:
        async with client.stream(
            "POST",
            AI_GATEWAY_URL,
            json={**payload, "stream": True},
            headers=headers,
        ) as response:
            if response.status_code >= 400:
                raise HTTPException(
                    status_code=502, detail="AI_GATEWAY_STREAM_ERROR"
                )
            async for chunk in response.aiter_bytes():
                if chunk:
                    yield chunk


@app.post("/v1/chat/stream")
async def chat_stream(request: ChatRequest) -> StreamingResponse:
    payload = {
        "messages": [message.model_dump() for message in request.messages],
        "model": request.model or AI_GATEWAY_MODEL,
    }
    if request.temperature is not None:
        payload["temperature"] = request.temperature

    return StreamingResponse(
        stream_gateway(payload),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
