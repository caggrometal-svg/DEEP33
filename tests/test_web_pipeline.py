from __future__ import annotations

import asyncio
import time

from backend.search.web_pipeline import (
    FetchPolicy,
    adaptive_timeout_seconds,
    fetch_source_quorum,
    record_fetch_metric,
    reset_domain_fetch_profiles,
)


def test_adaptive_timeout_learns_domain_latency_and_caps_timeout() -> None:
    reset_domain_fetch_profiles()
    policy = FetchPolicy(
        base_timeout_seconds=2.0,
        min_timeout_seconds=0.8,
        max_timeout_seconds=4.0,
    )
    url = "https://slow.example.org/article"

    assert adaptive_timeout_seconds(url, policy) == 2.0

    record_fetch_metric(
        url,
        ok=False,
        latency_seconds=2.1,
        status="FETCH_TIMEOUT",
        policy=policy,
    )
    learned = adaptive_timeout_seconds(url, policy)

    assert learned > 2.0
    assert learned <= 4.0

    for _ in range(10):
        record_fetch_metric(
            url,
            ok=False,
            latency_seconds=4.0,
            status="FETCH_TIMEOUT",
            policy=policy,
        )
    assert adaptive_timeout_seconds(url, policy) == 4.0


def test_blocked_domain_does_not_receive_unbounded_timeout_budget() -> None:
    reset_domain_fetch_profiles()
    policy = FetchPolicy(
        base_timeout_seconds=2.0,
        min_timeout_seconds=0.8,
        max_timeout_seconds=5.0,
    )
    url = "https://blocked.example.com/article"

    for _ in range(4):
        record_fetch_metric(
            url,
            ok=False,
            latency_seconds=0.2,
            status="WEB_FETCH_HTTP_403",
            policy=policy,
        )

    assert adaptive_timeout_seconds(url, policy) < 2.0


def test_fetch_quorum_cancels_slow_tail_after_three_quality_sources() -> None:
    sources = [
        {"url": f"https://source-{index}.example/article", "source_domain": f"source-{index}.example"}
        for index in range(5)
    ]
    cancelled = []

    async def fetcher(source, _timeout):
        index = int(str(source["source_domain"]).split("-")[1].split(".")[0])
        if index < 3:
            await asyncio.sleep(0.01 * (index + 1))
            return {
                "url": source["url"],
                "domain": source["source_domain"],
                "text": ("verified evidence " * 90),
            }
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            cancelled.append(source["source_domain"])
            raise
        return None

    async def exercise():
        policy = FetchPolicy(
            base_timeout_seconds=1.0,
            min_timeout_seconds=0.8,
            max_timeout_seconds=2.0,
            global_budget_seconds=1.0,
            concurrency=5,
            target_sources=3,
            max_sources=5,
            min_text_chars=240,
            min_total_chars=2400,
        )
        started = time.perf_counter()
        result = await fetch_source_quorum(sources, fetcher, policy=policy)
        return result, time.perf_counter() - started

    result, elapsed = asyncio.run(exercise())

    assert result.ok is True
    assert result.quorum_reached is True
    assert result.fetched == 3
    assert result.distinct_domains == 3
    assert result.total_text_chars >= 2400
    assert result.cancelled == 2
    assert len(cancelled) == 2
    assert elapsed < 0.5


def test_fetch_quorum_keeps_degraded_evidence_when_target_is_not_reached() -> None:
    sources = [
        {"url": "https://a.example/article", "source_domain": "a.example"},
        {"url": "https://b.example/article", "source_domain": "b.example"},
        {"url": "https://c.example/article", "source_domain": "c.example"},
    ]

    async def fetcher(source, _timeout):
        if source["source_domain"] == "a.example":
            return {
                "url": source["url"],
                "domain": source["source_domain"],
                "text": "verified evidence " * 100,
            }
        return None

    result = asyncio.run(fetch_source_quorum(
        sources,
        fetcher,
        policy=FetchPolicy(
            global_budget_seconds=1.0,
            concurrency=3,
            target_sources=3,
            max_sources=5,
            min_text_chars=240,
            min_total_chars=1000,
        ),
    ))

    assert result.ok is True
    assert result.status == "DEGRADED"
    assert result.fetched == 1
    assert result.quorum_reached is False
