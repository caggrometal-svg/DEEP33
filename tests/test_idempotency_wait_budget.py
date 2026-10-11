from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from backend import main as app_main
from backend.memory import MemoryUnavailableError


class FakeMemory:
    enabled = True

    def __init__(self, claim_result, status_result=None):
        self.claim_result = claim_result
        self.status_result = status_result
        self.status_calls = 0

    async def idempotency_claim(self, *args, **kwargs):
        return self.claim_result

    async def idempotency_status(self, *args, **kwargs):
        self.status_calls += 1
        if isinstance(self.status_result, Exception):
            raise self.status_result
        return self.status_result


def test_shared_idempotency_wait_returns_completed_result(monkeypatch):
    fake_memory = FakeMemory(
        {"state": "IN_PROGRESS"},
        {"state": "COMPLETED", "response": {"text": "original response"}},
    )
    monkeypatch.setattr(app_main, "memory", fake_memory)

    async def run():
        state, record = await app_main.shared_idempotency_claim(
            "session-1", "turn-1", "deep33.ai.generate", "hash-1"
        )
        assert state == "COMPLETED"
        assert record["response"]["text"] == "original response"

    asyncio.run(run())
    assert fake_memory.status_calls == 1


def test_shared_idempotency_wait_is_bounded(monkeypatch):
    fake_memory = FakeMemory({"state": "IN_PROGRESS"}, {"state": "IN_PROGRESS"})
    monkeypatch.setattr(app_main, "memory", fake_memory)
    monkeypatch.setattr(app_main, "IDEMPOTENCY_WAIT_SECONDS", 0.01)

    async def run():
        with pytest.raises(HTTPException) as error:
            await app_main.shared_idempotency_claim(
                "session-1", "turn-2", "deep33.ai.generate", "hash-2"
            )
        assert error.value.status_code == 504
        assert error.value.detail == "IDEMPOTENCY_IN_PROGRESS"

    asyncio.run(run())
    assert fake_memory.status_calls == 0


def test_idempotency_status_failure_never_uses_local_claim(monkeypatch):
    fake_memory = FakeMemory(
        {"state": "IN_PROGRESS"},
        MemoryUnavailableError("status unavailable"),
    )
    local_claim = lambda *args: ("CLAIMED", {"_storage": "local"})
    local_claim_called = False

    def track_local_claim(*args):
        nonlocal local_claim_called
        local_claim_called = True
        return local_claim(*args)

    monkeypatch.setattr(app_main, "memory", fake_memory)
    monkeypatch.setattr(app_main, "IDEMPOTENCY_WAIT_SECONDS", 2.0)
    monkeypatch.setattr(app_main, "local_idempotency_claim", track_local_claim)

    async def run():
        with pytest.raises(HTTPException) as error:
            await app_main.shared_idempotency_claim(
                "session-1", "turn-3", "deep33.ai.generate", "hash-3"
            )
        assert error.value.status_code == 503
        assert error.value.detail == "IDEMPOTENCY_STATUS_UNAVAILABLE"

    asyncio.run(run())
    assert fake_memory.status_calls == 1
    assert local_claim_called is False
