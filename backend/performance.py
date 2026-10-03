from __future__ import annotations

from collections import defaultdict, deque
from threading import Lock
import time

STAGES = (
    "T2_BACKEND_RECEIVED",
    "T3_CONTEXT_PREPARED",
    "T4_SEARCH_STARTED",
    "T5_SEARCH_FINISHED",
    "T6_INFERENCE_STARTED",
    "T7_FIRST_TOKEN",
    "T8_STREAM_FINISHED",
    "T9_PERSISTENCE_FINISHED",
)

_ORDER = {stage: index for index, stage in enumerate(STAGES)}
_lock = Lock()
_traces: dict[str, dict[str, float]] = {}
_durations: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=500))


def mark(request_id: str, stage: str) -> None:
    request_id = str(request_id or "").strip()
    stage = str(stage or "").strip()
    if not request_id or stage not in _ORDER:
        return
    now = time.perf_counter()
    with _lock:
        trace = _traces.setdefault(request_id, {})
        if stage in trace:
            return
        previous_index = _ORDER[stage] - 1
        trace[stage] = now
        if previous_index >= 0:
            previous = STAGES[previous_index]
            if previous in trace:
                _durations[previous + "_TO_" + stage].append(
                    (now - trace[previous]) * 1000
                )


def snapshot() -> dict:
    with _lock:
        series = {}
        for name, values in _durations.items():
            items = sorted(values)
            if not items:
                continue

            def percentile(percent: float) -> float:
                index = min(
                    len(items) - 1,
                    max(0, int(round((percent / 100) * (len(items) - 1)))),
                )
                return round(items[index], 2)

            series[name] = {
                "count": len(items),
                "p50_ms": percentile(50),
                "p95_ms": percentile(95),
            }
        return {
            "stages": list(STAGES),
            "transitions": series,
            "active_traces": len(_traces),
        }
