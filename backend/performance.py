from __future__ import annotations

from collections import defaultdict, deque
from threading import Lock
import time


# Full end-to-end marker contract. Backend services emit T2-T9; Android emits
# T0, T1, T10 and T11. Keeping one canonical order makes the trace contract
# explicit and prevents silent stage omissions.
STAGES = (
    "T0_INPUT",
    "T1_REQUEST_SENT",
    "T2_BACKEND_RECEIVED",
    "T3_CONTEXT_PREPARED",
    "T4_SEARCH_STARTED",
    "T5_SEARCH_FINISHED",
    "T6_INFERENCE_STARTED",
    "T7_FIRST_TOKEN",
    "T8_STREAM_FINISHED",
    "T9_PERSISTENCE_FINISHED",
    "T10_FIRST_VISIBLE",
    "T11_FIRST_SPOKEN",
)

_BACKEND_STAGES = set(STAGES[2:10])
_ORDER = {stage: index for index, stage in enumerate(STAGES)}
_lock = Lock()
_traces: dict[str, dict[str, float]] = {}
_trace_order: deque[str] = deque(maxlen=500)
_durations: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=500))


def mark(request_id: str, stage: str) -> None:
    request_id = str(request_id or "").strip()
    stage = str(stage or "").strip()
    if not request_id or stage not in _ORDER:
        return

    now = time.perf_counter()
    with _lock:
        trace = _traces.setdefault(request_id, {})
        if request_id not in _trace_order:
            if len(_trace_order) == _trace_order.maxlen:
                oldest = _trace_order[0]
                _traces.pop(oldest, None)
            _trace_order.append(request_id)

        if stage in trace:
            return

        trace[stage] = now
        previous_index = _ORDER[stage] - 1
        if previous_index >= 0:
            previous = STAGES[previous_index]
            if previous in trace:
                _durations[f"{previous}_TO_{stage}"].append(
                    (now - trace[previous]) * 1000
                )


def trace_snapshot(request_id: str) -> dict:
    request_id = str(request_id or "").strip()
    if not request_id:
        return {"request_id": "", "stages": {}, "transitions_ms": {}}

    with _lock:
        trace = dict(_traces.get(request_id, {}))

    first = next(iter(trace.values()), None)
    stage_times = {}
    if first is not None:
        stage_times = {
            stage: round(timestamp - first, 6)
            for stage, timestamp in trace.items()
        }

    transitions = {}
    ordered = sorted(
        ((stage, timestamp) for stage, timestamp in trace.items()),
        key=lambda item: _ORDER[item[0]],
    )
    for index in range(1, len(ordered)):
        previous_stage, previous_time = ordered[index - 1]
        stage, stage_time = ordered[index]
        transitions[f"{previous_stage}_TO_{stage}"] = round(
            (stage_time - previous_time) * 1000, 2
        )

    return {
        "request_id": request_id,
        "stages": stage_times,
        "transitions_ms": transitions,
        "completed_stage_count": len(trace),
        "expected_stage_count": len(STAGES),
    }


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(
        len(ordered) - 1,
        max(0, int(round((percentile / 100) * (len(ordered) - 1)))),
    )
    return round(ordered[index], 2)


def _series_snapshot(values: deque[float] | list[float]) -> dict[str, float | int | None]:
    snapshot = list(values)
    return {
        "count": len(snapshot),
        "p50_ms": _percentile(snapshot, 50),
        "p95_ms": _percentile(snapshot, 95),
    }


def snapshot() -> dict:
    with _lock:
        series = {
            name: _series_snapshot(values)
            for name, values in _durations.items()
            if values
        }

    transition_aliases = {
        "context_prepare_ms": "T2_BACKEND_RECEIVED_TO_T3_CONTEXT_PREPARED",
        "search_ms": "T4_SEARCH_STARTED_TO_T5_SEARCH_FINISHED",
        "ttft_ms": "T6_INFERENCE_STARTED_TO_T7_FIRST_TOKEN",
        "stream_ms": "T7_FIRST_TOKEN_TO_T8_STREAM_FINISHED",
        "persistence_ms": "T8_STREAM_FINISHED_TO_T9_PERSISTENCE_FINISHED",
        "backend_total_ms": "T2_BACKEND_RECEIVED_TO_T9_PERSISTENCE_FINISHED",
    }
    summary = {
        name: series.get(transition, _series_snapshot([]))
        for name, transition in transition_aliases.items()
    }

    with _lock:
        active_traces = len(_traces)

    return {
        "stages": list(STAGES),
        "backend_stages": [stage for stage in STAGES if stage in _BACKEND_STAGES],
        "android_stages": [stage for stage in STAGES if stage not in _BACKEND_STAGES],
        "transitions": series,
        "summary": summary,
        "active_traces": active_traces,
    }
