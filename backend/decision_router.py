"""Universal query decision router for DEEP33.

Classifies every user query before inference and selects the cheapest trustworthy
source: runtime clock, dedicated realtime API, web search, or model knowledge.
The router is deliberately provider-agnostic so Node/Edge callers can consume
the same decision contract through the FastAPI backend.
"""
from __future__ import annotations

import asyncio
import hashlib
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

import httpx


class DecisionRoute:
    INTERNAL = "internal"
    CLOCK = "clock"
    WEATHER = "weather"
    SEISMIC = "seismic"
    FINANCE = "finance"
    WEB = "web"


@dataclass(frozen=True, slots=True)
class Decision:
    route: str
    confidence: float
    reason: str
    query: str
    cache_ttl_seconds: float


@dataclass(slots=True)
class Evidence:
    ok: bool
    route: str
    provider: str | None = None
    source_url: str | None = None
    retrieved_at: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def system_context(self) -> str:
        if not self.ok:
            return ""
        return (
            "DATOS ESTRUCTURADOS OBTENIDOS POR EL ENRUTADOR UNIVERSAL DE DEEP33. "
            "Son datos externos verificados para esta consulta. Úsalos como hechos; "
            "no inventes campos ausentes ni sustituyas valores por conocimiento previo.\n"
            f"Proveedor: {self.provider}\n"
            f"Obtenido: {self.retrieved_at}\n"
            f"Fuente: {self.source_url}\n"
            f"Datos: {self.data}"
        )


@dataclass(slots=True)
class _CacheItem:
    expires_at: float
    value: Evidence


class DecisionCache:
    def __init__(self, max_items: int = 256) -> None:
        self.max_items = max(16, max_items)
        self._items: dict[str, _CacheItem] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def key(route: str, query: str, context: str = "") -> str:
        raw = f"{route}|{query.strip().casefold()}|{context.strip().casefold()}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    async def get(self, key: str) -> Evidence | None:
        async with self._lock:
            item = self._items.get(key)
            if item is None:
                return None
            if item.expires_at <= time.monotonic():
                self._items.pop(key, None)
                return None
            return item.value

    async def put(self, key: str, value: Evidence, ttl: float) -> None:
        async with self._lock:
            if len(self._items) >= self.max_items:
                oldest = min(self._items, key=lambda k: self._items[k].expires_at)
                self._items.pop(oldest, None)
            self._items[key] = _CacheItem(time.monotonic() + max(1.0, ttl), value)


_CLOCK_RE = re.compile(
    r"\b(?:hora|fecha|día|dia|date|time)\b.*\b(?:actual|ahora|hoy|today|current)\b|"
    r"\b(?:qué|que|cuál|cual|dime)\s+(?:hora|fecha|día|dia)\b",
    re.I,
)
_WEATHER_RE = re.compile(
    r"\b(?:clima|tiempo|temperatura|pron[oó]stico|pronostico|lluvia|llover|"
    r"humedad|calor|fr[ií]o|viento|tormenta|paraguas|weather|forecast)\b",
    re.I,
)
_SEISMIC_RE = re.compile(
    r"\b(?:sismo|sismos|terremoto|terremotos|temblor|temblores|earthquake|earthquakes)\b",
    re.I,
)
_FINANCE_RE = re.compile(
    r"\b(?:d[oó]lar|dolar|tipo de cambio|cotizaci[oó]n|divisa|divisas|"
    r"euro|uf|utm|bitcoin|btc|usd|clp|precio de|valor de)\b",
    re.I,
)
_REALTIME_RE = re.compile(
    r"\b(?:hoy|ahora|actual|actualmente|en este momento|último|última|últimos|"
    r"últimas|reciente|recientes|vigente|en tiempo real|ahora mismo|esta semana|"
    r"este mes|live|today|current|latest|right now)\b",
    re.I,
)
_RESEARCH_RE = re.compile(
    r"\b(?:investiga|investigación|busca|buscar|navega|internet|web|verifica|"
    r"comprueba|contrasta|evidencia|fuentes|fact[- ]?check)\b",
    re.I,
)
_PRICE_RE = re.compile(r"\b(?:precio|valor|cotizaci[oó]n|cu[aá]nto vale|cu[aá]nto est[aá])\b", re.I)


def classify_query(query: str) -> Decision:
    cleaned = " ".join(str(query or "").split()).strip()
    lowered = cleaned.casefold()
    if not cleaned:
        return Decision(Decision.INTERNAL, 1.0, "empty_query", cleaned, 0.0)
    if _CLOCK_RE.search(cleaned):
        return Decision(Decision.CLOCK, 1.0, "runtime_clock_is_authoritative", cleaned, 1.0)
    if _WEATHER_RE.search(cleaned):
        return Decision(
            Decision.WEATHER, 0.98,
            "weather_requires_current_provider_data", cleaned, 45.0,
        )
    if _SEISMIC_RE.search(cleaned):
        return Decision(
            Decision.SEISMIC, 0.99,
            "seismic_data_is_time_sensitive", cleaned, 15.0,
        )
    if _FINANCE_RE.search(cleaned) and _REALTIME_RE.search(cleaned):
        return Decision(
            Decision.FINANCE, 0.99,
            "financial_quote_is_time_sensitive", cleaned, 20.0,
        )
    if _FINANCE_RE.search(cleaned) and (
        re.search(r"\b(?:d[oó]lar|dolar|tipo de cambio|cotizaci[oó]n|uf|utm)\b", lowered)
        or _PRICE_RE.search(cleaned)
    ):
        return Decision(
            Decision.FINANCE, 0.95,
            "financial_indicator_should_use_live_data", cleaned, 20.0,
        )
    if _RESEARCH_RE.search(cleaned) or _REALTIME_RE.search(cleaned):
        return Decision(
            Decision.WEB, 0.92,
            "explicit_current_or_web_request", cleaned, 20.0,
        )
    return Decision(
        Decision.INTERNAL, 0.86,
        "stable_general_knowledge_can_use_model", cleaned, 0.0,
    )


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def fetch_seismic(
    query: str,
    *,
    timeout_seconds: float = 3.0,
    cache: DecisionCache | None = None,
) -> Evidence:
    """Fetch recent worldwide earthquakes from USGS.

    The query window is inferred from natural language. Default is the last
    10 minutes because that is a common DEEP33 realtime question.
    """
    key = DecisionCache.key(DecisionRoute.SEISMIC, query, "usgs")
    if cache:
        cached = await cache.get(key)
        if cached:
            return cached

    lowered = query.casefold()
    minutes = 10
    match = re.search(r"(\d+)\s*(?:minutos?|mins?)", lowered)
    if match:
        minutes = max(1, min(1440, int(match.group(1))))
    end = datetime.now(timezone.utc)
    start = end.timestamp() - minutes * 60
    url = "https://earthquake.usgs.gov/fdsnws/event/1/query"
    params = {
        "format": "geojson",
        "starttime": datetime.fromtimestamp(start, timezone.utc).isoformat(),
        "endtime": end.isoformat(),
        "minmagnitude": "0",
        "orderby": "time",
        "limit": "2000",
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_seconds, connect=1.0)) as client:
            response = await client.get(url, params=params, headers={"User-Agent": "DEEP33/UniversalRouter"})
            response.raise_for_status()
            payload = response.json()
        features = payload.get("features")
        if not isinstance(features, list):
            raise ValueError("SEISMIC_INVALID_PAYLOAD")
        events = []
        for item in features:
            if not isinstance(item, dict):
                continue
            props = item.get("properties") or {}
            coords = (item.get("geometry") or {}).get("coordinates") or []
            events.append({
                "id": item.get("id"),
                "magnitude": props.get("mag"),
                "place": props.get("place"),
                "time_ms": props.get("time"),
                "url": props.get("url"),
                "longitude": coords[0] if len(coords) > 0 else None,
                "latitude": coords[1] if len(coords) > 1 else None,
                "depth_km": coords[2] if len(coords) > 2 else None,
            })
        evidence = Evidence(
            ok=True,
            route=DecisionRoute.SEISMIC,
            provider="USGS Earthquake Hazards Program",
            source_url=str(response.url),
            retrieved_at=_iso_now(),
            data={
                "window_minutes": minutes,
                "start": params["starttime"],
                "end": params["endtime"],
                "count": len(events),
                "events": events,
            },
        )
    except Exception as exc:
        evidence = Evidence(
            ok=False, route=DecisionRoute.SEISMIC,
            error=f"SEISMIC_PROVIDER_FAILED:{type(exc).__name__}",
        )
    if cache and evidence.ok:
        await cache.put(key, evidence, 15.0)
    return evidence


async def fetch_finance(
    query: str,
    *,
    timeout_seconds: float = 3.0,
    cache: DecisionCache | None = None,
) -> Evidence:
    """Fetch a current USD/CLP quote, with an official Chilean fallback."""
    key = DecisionCache.key(DecisionRoute.FINANCE, query, "usdclp")
    if cache:
        cached = await cache.get(key)
        if cached:
            return cached

    lowered = query.casefold()
    if not re.search(r"\b(?:d[oó]lar|dolar|usd|tipo de cambio)\b", lowered):
        return Evidence(
            ok=False, route=DecisionRoute.FINANCE,
            error="FINANCE_INDICATOR_NOT_SUPPORTED",
        )

    yahoo_url = "https://query1.finance.yahoo.com/v8/finance/chart/CLP=X"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_seconds, connect=1.0)) as client:
            response = await client.get(
                yahoo_url,
                params={"interval": "1m", "range": "1d"},
                headers={"User-Agent": "DEEP33/UniversalRouter"},
            )
            response.raise_for_status()
            payload = response.json()
        meta = ((payload.get("chart") or {}).get("result") or [{}])[0].get("meta") or {}
        value = meta.get("regularMarketPrice")
        quote_time = meta.get("regularMarketTime")
        if not isinstance(value, (int, float)) or not quote_time:
            raise ValueError("FINANCE_YAHOO_INVALID_QUOTE")
        quote_dt = datetime.fromtimestamp(float(quote_time), timezone.utc)
        evidence = Evidence(
            ok=True,
            route=DecisionRoute.FINANCE,
            provider="Yahoo Finance (USD/CLP)",
            source_url=yahoo_url,
            retrieved_at=_iso_now(),
            data={
                "indicator": "USD/CLP",
                "currency": "CLP por USD",
                "value": float(value),
                "market_timestamp": quote_dt.isoformat(),
                "unit": "CLP/USD",
                "quote_status": "delayed_or_market_feed",
            },
        )
    except Exception as yahoo_exc:
        logger_error = f"{type(yahoo_exc).__name__}"
        try:
            fallback_url = "https://mindicador.cl/api/dolar"
            async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_seconds, connect=1.0)) as client:
                response = await client.get(
                    fallback_url,
                    headers={"User-Agent": "DEEP33/UniversalRouter"},
                )
                response.raise_for_status()
                payload = response.json()
            serie = payload.get("serie")
            latest = serie[0] if isinstance(serie, list) and serie else None
            value = latest.get("valor") if isinstance(latest, dict) else None
            date = latest.get("fecha") if isinstance(latest, dict) else None
            if not isinstance(value, (int, float)) or not date:
                raise ValueError("FINANCE_MINDICADOR_INVALID_QUOTE")
            evidence = Evidence(
                ok=True,
                route=DecisionRoute.FINANCE,
                provider="MiIndicador.cl (official fallback)",
                source_url=fallback_url,
                retrieved_at=_iso_now(),
                data={
                    "indicator": "dólar observado",
                    "currency": "CLP por USD",
                    "value": float(value),
                    "date": str(date),
                    "unit": "CLP/USD",
                    "quote_status": "official_daily_indicator",
                },
            )
        except Exception as fallback_exc:
            evidence = Evidence(
                ok=False,
                route=DecisionRoute.FINANCE,
                error=(
                    "FINANCE_PROVIDERS_FAILED:"
                    f"yahoo={logger_error}:fallback={type(fallback_exc).__name__}"
                ),
            )
    if cache and evidence.ok:
        await cache.put(key, evidence, 20.0)
    return evidence

async def validate_evidence(evidence: Evidence) -> Evidence:
    """Reject incomplete/stale structured data before it reaches the model."""
    if not evidence.ok:
        return evidence
    if not evidence.provider or not evidence.source_url or not evidence.retrieved_at:
        evidence.ok = False
        evidence.error = "EVIDENCE_METADATA_MISSING"
        return evidence
    try:
        retrieved = datetime.fromisoformat(evidence.retrieved_at.replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - retrieved.astimezone(timezone.utc)).total_seconds()
        if age < -5 or age > 120:
            evidence.ok = False
            evidence.error = "EVIDENCE_STALE"
            return evidence
    except ValueError:
        evidence.ok = False
        evidence.error = "EVIDENCE_TIMESTAMP_INVALID"
        return evidence
    if evidence.route == DecisionRoute.SEISMIC:
        data = evidence.data
        if not isinstance(data.get("count"), int) or not isinstance(data.get("events"), list):
            evidence.ok = False
            evidence.error = "SEISMIC_VALIDATION_FAILED"
    elif evidence.route == DecisionRoute.FINANCE:
        data = evidence.data
        if not isinstance(data.get("value"), (int, float)) or not (data.get("date") or data.get("market_timestamp")):
            evidence.ok = False
            evidence.error = "FINANCE_VALIDATION_FAILED"
    elif evidence.route == DecisionRoute.WEATHER:
        if not evidence.data:
            evidence.ok = False
            evidence.error = "WEATHER_VALIDATION_FAILED"
    return evidence


class UniversalDecisionRouter:
    def __init__(self) -> None:
        self.cache = DecisionCache()
        self._inflight: dict[str, asyncio.Task[Evidence]] = {}
        self._lock = asyncio.Lock()

    async def _singleflight(
        self, key: str, operation: Callable[[], Awaitable[Evidence]]
    ) -> Evidence:
        async with self._lock:
            task = self._inflight.get(key)
            if task is None:
                task = asyncio.create_task(operation())
                self._inflight[key] = task
        try:
            return await task
        finally:
            if task.done():
                async with self._lock:
                    if self._inflight.get(key) is task:
                        self._inflight.pop(key, None)

    async def resolve(
        self,
        query: str,
        *,
        messages: list[dict[str, Any]] | None = None,
        weather: dict[str, Any] | None = None,
        weather_builder: Callable[[list[dict[str, Any]], dict[str, Any]], str] | None = None,
        weather_resolver: Callable[[list[dict[str, Any]]], Awaitable[dict[str, Any] | None]] | None = None,
    ) -> tuple[Decision, Evidence | None]:
        decision = classify_query(query)
        if decision.route == DecisionRoute.INTERNAL:
            return decision, None
        if decision.route == DecisionRoute.CLOCK:
            return decision, Evidence(
                ok=True,
                route=DecisionRoute.CLOCK,
                provider="DEEP33 runtime clock",
                source_url="runtime://clock",
                retrieved_at=_iso_now(),
                data={"timestamp": _iso_now()},
            )
        if decision.route == DecisionRoute.WEATHER:
            data = weather
            if data is None and weather_resolver and messages is not None:
                key = DecisionCache.key(DecisionRoute.WEATHER, query, str(messages[-1] if messages else ""))
                cached = await self.cache.get(key)
                if cached:
                    return decision, cached
                data = await self._singleflight(
                    key,
                    lambda: self._weather_operation(messages, weather_resolver, weather_builder),
                )
                return decision, await validate_evidence(data)
            if data is None:
                return decision, Evidence(False, DecisionRoute.WEATHER, error="WEATHER_DATA_UNAVAILABLE")
            evidence = Evidence(
                ok=True, route=DecisionRoute.WEATHER, provider="Open-Meteo",
                source_url=str(data.get("source_url") or "https://api.open-meteo.com/v1/forecast"),
                retrieved_at=_iso_now(), data=data,
            )
            return decision, await validate_evidence(evidence)
        if decision.route == DecisionRoute.SEISMIC:
            key = DecisionCache.key(DecisionRoute.SEISMIC, query, "usgs")
            return decision, await self._singleflight(
                key, lambda: fetch_seismic(query, cache=self.cache)
            )
        if decision.route == DecisionRoute.FINANCE:
            key = DecisionCache.key(DecisionRoute.FINANCE, query, "mindicador")
            return decision, await self._singleflight(
                key, lambda: fetch_finance(query, cache=self.cache)
            )
        return decision, None

    async def _weather_operation(
        self,
        messages: list[dict[str, Any]],
        resolver: Callable[[list[dict[str, Any]]], Awaitable[dict[str, Any] | None]],
        builder: Callable[[list[dict[str, Any]], dict[str, Any]], str] | None,
    ) -> Evidence:
        data = await resolver(messages)
        if data is None:
            return Evidence(False, DecisionRoute.WEATHER, error="WEATHER_PROVIDER_UNAVAILABLE")
        return Evidence(
            ok=True,
            route=DecisionRoute.WEATHER,
            provider=str(data.get("provider") or "Open-Meteo"),
            source_url=str(data.get("source_url") or "https://api.open-meteo.com/v1/forecast"),
            retrieved_at=_iso_now(),
            data=data,
        )


universal_router = UniversalDecisionRouter()
_PRICE_RE = re.compile(r"\\b(?:precio|valor|cotizaci[oó]n|cu[aá]nto vale|cu[aá]nto est[aá])\\b", re.I)
