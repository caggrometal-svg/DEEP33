from __future__ import annotations

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

APP_NAME = "DEEP33 Backend"
APP_VERSION = "0.1.2"

# Network remains owned by the HTTP API; provider-specific transport/configuration
# lives behind backend.gateway.AIGateway so the provider can be replaced without
# changing DEEP33's public API.
NETWORK_CHECK_URL = os.getenv(
    "NETWORK_CHECK_URL", "https://www.google.com/generate_204"
).strip()
NETWORK_TIMEOUT = float(os.getenv("NETWORK_TIMEOUT_SECONDS", "8"))

gateway = AIGateway()
AI_GATEWAY_MODEL = gateway.config.model

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
    model_pass = (
        gateway_status["gateway"] == "PASS"
        and bool(gateway_status.get("model"))
    )

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
        },
        "network": network,
        "gateway": gateway_status,
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
    async for chunk in gateway.stream(payload):
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
