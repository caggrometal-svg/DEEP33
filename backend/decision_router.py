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
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse

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
            value = item.value
            return replace(value, data=dict(value.data))

    async def put(self, key: str, value: Evidence, ttl: float) -> None:
        async with self._lock:
            if key not in self._items and len(self._items) >= self.max_items:
                oldest = min(self._items, key=lambda k: self._items[k].expires_at)
                self._items.pop(oldest, None)
            self._items[key] = _CacheItem(
                time.monotonic() + max(1.0, ttl),
                replace(value, data=dict(value.data)),
            )


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
    r"euro|uf|utm|bitcoin|btc|usd|clp|acciones?|bolsa|mercado burs[aá]til|"
    r"stock(?:s)?|share price|nasdaq|nyse|s&p 500)\b",
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
_PRICE_RE = re.compile(
    r"\b(?:precio(?:s)?|valor(?:es)?|cotizaci[oó]n|cu[aá]nto cuesta|cu[aá]nto vale|"
    r"price(?:s)?|pricing|cost(?:s)?|coste(?:s)?|costos?|cu[aá]nto est[aá])\b",
    re.I,
)
_DOMAIN_SPECIFIC_WEB_RE = re.compile(
    r"\b(?:"
    r"empresa(?:s)?|compa[nñ][ií]a(?:s)?|marca(?:s)?|fabricante(?:s)?|producto(?:s)?|modelo(?:s)?|"
    r"company|companies|brand|manufacturer|product(?:s)?|model(?:s)?|"
    r"precio(?:s)?|price(?:s)?|pricing|cost(?:s)?|coste(?:s)?|costos?|"
    r"especificaci[oó]n(?:es)?|ficha t[eé]cnica|manual(?:es)?|documentaci[oó]n(?: t[eé]cnica| oficial)?|"
    r"api|sdk|repositorio|github|framework|biblioteca|librer[ií]a|changelog|release notes|"
    r"versi[oó]n(?:es)?|version(?:s)?|documentation|technical docs?|official docs?|"
    r"comparar|compara(?:ci[oó]n|tiva)?|comparisons?|compare|versus|vs\.?|alternativa(?:s)?|"
    r"rese[nñ]as|opiniones|reviews?|ratings?|mejores|best|"
    r"noticia(?:s)?|news|titular(?:es)?|reportaje(?:s)?|"
    r"art[ií]culo(?:s)? cient[ií]fico(?:s)?|papers?|preprints?|estudio(?:s)? cient[ií]fico(?:s)?|"
    r"publicaci[oó]n(?:es)? cient[ií]fica(?:s)?|investigaci[oó]n(?:es)? reciente(?:s)?|"
    r"open data|datos abiertos|datos p[uú]blicos|dataset(?:s)?|estad[ií]sticas oficiales|"
    r"base de datos|portal de transparencia|transparencia|organismo oficial|sitio oficial|"
    r"ministerio|government data|public records|official website|regulaci[oó]n|normativa|"
    r"ley vigente|reglamento|foro(?:s)?|forum(?:s)?|reddit|stackoverflow|stack overflow|"
    r"d[oó]nde comprar|d[oó]nde lo venden|d[oó]nde venden|d[oó]nde adquirir|"
    r"comprar online|comprar en l[ií]nea|comprar|venden|venta|tienda(?:s)?|"
    r"where to buy|buy online|shop(?:ping)?|store(?:s)?|available to buy"
    r")\b",
    re.I,
)


def classify_query(query: str) -> Decision:
    cleaned = " ".join(str(query or "").split()).strip()
    lowered = cleaned.casefold()
    if not cleaned:
        return Decision(DecisionRoute.INTERNAL, 1.0, "empty_query", cleaned, 0.0)
    if _CLOCK_RE.search(cleaned):
        return Decision(DecisionRoute.CLOCK, 1.0, "runtime_clock_is_authoritative", cleaned, 1.0)
    if _WEATHER_RE.search(cleaned):
        return Decision(
            DecisionRoute.WEATHER, 0.98,
            "weather_requires_current_provider_data", cleaned, 45.0,
        )
    if _SEISMIC_RE.search(cleaned):
        return Decision(
            DecisionRoute.SEISMIC, 0.99,
            "seismic_data_is_time_sensitive", cleaned, 15.0,
        )
    if _FINANCE_RE.search(cleaned) and _REALTIME_RE.search(cleaned):
        return Decision(
            DecisionRoute.FINANCE, 0.99,
            "financial_quote_is_time_sensitive", cleaned, 20.0,
        )
    if _FINANCE_RE.search(cleaned) and (
        re.search(r"\b(?:d[oó]lar|dolar|tipo de cambio|cotizaci[oó]n|uf|utm)\b", lowered)
        or _PRICE_RE.search(cleaned)
    ):
        return Decision(
            DecisionRoute.FINANCE, 0.95,
            "financial_indicator_should_use_live_data", cleaned, 20.0,
        )
    if _DOMAIN_SPECIFIC_WEB_RE.search(cleaned) or _PRICE_RE.search(cleaned):
        return Decision(
            DecisionRoute.WEB, 0.90,
            "specific_external_evidence_requested", cleaned, 20.0,
        )
    if _RESEARCH_RE.search(cleaned) or _REALTIME_RE.search(cleaned):
        return Decision(
            DecisionRoute.WEB, 0.92,
            "explicit_current_or_web_request", cleaned, 20.0,
        )
    return Decision(
        DecisionRoute.INTERNAL, 0.86,
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
        bounded_timeout = max(0.4, min(1.8, float(timeout_seconds)))
        async with httpx.AsyncClient(timeout=httpx.Timeout(bounded_timeout, connect=min(0.8, bounded_timeout))) as client:
            response = await asyncio.wait_for(
                client.get(url, params=params, headers={"User-Agent": "DEEP33/UniversalRouter"}),
                timeout=bounded_timeout,
            )
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
        primary_timeout = max(0.4, min(1.3, float(timeout_seconds)))
        async with httpx.AsyncClient(timeout=httpx.Timeout(primary_timeout, connect=min(0.7, primary_timeout))) as client:
            response = await asyncio.wait_for(
                client.get(
                    yahoo_url,
                    params={"interval": "1m", "range": "1d"},
                    headers={"User-Agent": "DEEP33/UniversalRouter"},
                ),
                timeout=primary_timeout,
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
            fallback_timeout = max(0.4, min(1.5, float(timeout_seconds)))
            async with httpx.AsyncClient(timeout=httpx.Timeout(fallback_timeout, connect=min(0.7, fallback_timeout))) as client:
                response = await asyncio.wait_for(
                    client.get(
                        fallback_url,
                        headers={"User-Agent": "DEEP33/UniversalRouter"},
                    ),
                    timeout=fallback_timeout,
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
    """Fail closed on malformed, missing, future-dated or stale evidence."""
    if not evidence.ok:
        return evidence
    if not evidence.provider or not evidence.source_url or not evidence.retrieved_at:
        evidence.ok = False
        evidence.error = "EVIDENCE_METADATA_MISSING"
        return evidence

    if evidence.route == DecisionRoute.CLOCK:
        if evidence.source_url != "runtime://clock":
            evidence.ok = False
            evidence.error = "EVIDENCE_SOURCE_URL_INVALID"
            return evidence
    else:
        parsed_url = urlparse(evidence.source_url)
        if parsed_url.scheme != "https" or not parsed_url.hostname:
            evidence.ok = False
            evidence.error = "EVIDENCE_SOURCE_URL_INVALID"
            return evidence

    try:
        retrieved = datetime.fromisoformat(evidence.retrieved_at.replace("Z", "+00:00"))
        if retrieved.tzinfo is None:
            raise ValueError("timestamp has no timezone")
        age = (datetime.now(timezone.utc) - retrieved.astimezone(timezone.utc)).total_seconds()
        if age < -5 or age > 120:
            evidence.ok = False
            evidence.error = "EVIDENCE_RETRIEVAL_TIMESTAMP_INVALID_OR_STALE"
            return evidence
    except (ValueError, TypeError):
        evidence.ok = False
        evidence.error = "EVIDENCE_TIMESTAMP_INVALID"
        return evidence

    data = evidence.data
    if not isinstance(data, dict) or not data:
        evidence.ok = False
        evidence.error = "EVIDENCE_DATA_MISSING"
        return evidence

    if evidence.route == DecisionRoute.SEISMIC:
        events = data.get("events")
        count = data.get("count")
        start_raw = data.get("start")
        end_raw = data.get("end")
        if not isinstance(events, list) or not isinstance(count, int) or count != len(events):
            evidence.ok = False
            evidence.error = "SEISMIC_VALIDATION_FAILED"
            return evidence
        try:
            window_start = datetime.fromisoformat(str(start_raw).replace("Z", "+00:00"))
            window_end = datetime.fromisoformat(str(end_raw).replace("Z", "+00:00"))
            if window_start.tzinfo is None or window_end.tzinfo is None:
                raise ValueError("seismic window must be timezone-aware")
            now = datetime.now(timezone.utc)
            from datetime import timedelta
            if window_end > now + timedelta(seconds=5) or window_start > window_end:
                raise ValueError("invalid seismic window")
            for event in events:
                if not isinstance(event, dict) or not isinstance(event.get("time_ms"), (int, float)):
                    raise ValueError("invalid seismic event")
                event_time = datetime.fromtimestamp(event["time_ms"] / 1000.0, timezone.utc)
                if event_time < window_start.astimezone(timezone.utc) or event_time > window_end.astimezone(timezone.utc) + timedelta(seconds=5):
                    raise ValueError("event outside requested time window")
        except (ValueError, TypeError, OverflowError, OSError):
            evidence.ok = False
            evidence.error = "SEISMIC_EVENT_TIME_VALIDATION_FAILED"
            return evidence

    elif evidence.route == DecisionRoute.FINANCE:
        value = data.get("value")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not (data.get("date") or data.get("market_timestamp")):
            evidence.ok = False
            evidence.error = "FINANCE_VALIDATION_FAILED"
            return evidence
        # This route reports CLP per USD; reject inverted-pair values and implausible quotes.
        if value < 50 or value > 10_000:
            evidence.ok = False
            evidence.error = "FINANCE_VALUE_OUT_OF_RANGE"
            return evidence
        quote_raw = data.get("market_timestamp")
        if quote_raw:
            try:
                quote_time = datetime.fromisoformat(str(quote_raw).replace("Z", "+00:00"))
                if quote_time.tzinfo is None:
                    raise ValueError("market timestamp has no timezone")
                quote_age = (datetime.now(timezone.utc) - quote_time.astimezone(timezone.utc)).total_seconds()
                if quote_age < -300 or quote_age > 36 * 3600:
                    evidence.ok = False
                    evidence.error = "FINANCE_MARKET_QUOTE_STALE"
                    return evidence
            except (ValueError, TypeError):
                evidence.ok = False
                evidence.error = "FINANCE_MARKET_TIMESTAMP_INVALID"
                return evidence
        else:
            try:
                daily_date = datetime.fromisoformat(str(data.get("date")).replace("Z", "+00:00"))
                if daily_date.tzinfo is None:
                    daily_date = daily_date.replace(tzinfo=timezone.utc)
                daily_age = (datetime.now(timezone.utc) - daily_date.astimezone(timezone.utc)).total_seconds()
                # Daily observed dollar: allow weekends/holidays, reject older than 72 hours.
                if daily_age < -300 or daily_age > 72 * 3600:
                    evidence.ok = False
                    evidence.error = "FINANCE_DAILY_INDICATOR_STALE"
                    return evidence
            except (ValueError, TypeError):
                evidence.ok = False
                evidence.error = "FINANCE_DATE_INVALID"
                return evidence

    elif evidence.route == DecisionRoute.WEATHER:
        if data.get("forecast") is True:
            required = (
                "forecast_date",
                "temperature_min_c",
                "temperature_max_c",
                "weather_code",
            )
            if any(key not in data or data.get(key) is None for key in required):
                evidence.ok = False
                evidence.error = "WEATHER_FORECAST_VALIDATION_FAILED"
                return evidence
            low = data.get("temperature_min_c")
            high = data.get("temperature_max_c")
            try:
                forecast_date = datetime.fromisoformat(str(data.get("forecast_date")))
                forecast_days = (forecast_date.date() - datetime.now(timezone.utc).date()).days
            except (TypeError, ValueError):
                evidence.ok = False
                evidence.error = "WEATHER_FORECAST_DATE_INVALID"
                return evidence
            offset = data.get("forecast_day_offset", 0)
            minimum_allowed_day = int(offset) - 1 if isinstance(offset, int) and not isinstance(offset, bool) else 0
            if (
                isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= 7
                or isinstance(low, bool) or isinstance(high, bool)
                or not isinstance(low, (int, float)) or not isinstance(high, (int, float))
                or low > high or not -100 <= low <= 70 or not -100 <= high <= 70
                or forecast_days < minimum_allowed_day or forecast_days > offset + 1
                or forecast_days > 8
            ):
                evidence.ok = False
                evidence.error = "WEATHER_FORECAST_VALUES_INVALID"
                return evidence
            rain = data.get("precipitation_probability_pct")
            if rain is not None and (
                isinstance(rain, bool) or not isinstance(rain, (int, float)) or not 0 <= rain <= 100
            ):
                evidence.ok = False
                evidence.error = "WEATHER_PRECIPITATION_PROBABILITY_INVALID"
                return evidence
        else:
            required = ("time", "temperature_c", "weather_code")
            if any(key not in data or data.get(key) is None for key in required):
                evidence.ok = False
                evidence.error = "WEATHER_VALIDATION_FAILED"
                return evidence
            temperature = data.get("temperature_c")
            if isinstance(temperature, bool) or not isinstance(temperature, (int, float)) or not -100 <= temperature <= 70:
                evidence.ok = False
                evidence.error = "WEATHER_TEMPERATURE_OUT_OF_RANGE"
                return evidence

    return evidence

def _format_number_es(value: Any, *, decimals: int = 2) -> str:
    number = float(value)
    if number.is_integer():
        return f"{int(number):,}".replace(",", ".")
    rendered = f"{number:,.{decimals}f}"
    return rendered.replace(",", "X").replace(".", ",").replace("X", ".")


def format_structured_answer(query: str, evidence: Evidence) -> str:
    """Render critical numeric facts deterministically instead of asking the LLM to restate them."""
    data = evidence.data
    if not evidence.ok:
        return "No pude verificar el dato actual con una fuente válida. No voy a inventar una cifra."
    if evidence.route == DecisionRoute.FINANCE:
        value = _format_number_es(data["value"])
        market_timestamp = data.get("market_timestamp")
        if market_timestamp:
            return (
                f"La cotización USD/CLP reportada por {evidence.provider} es "
                f"1 dólar estadounidense = {value} pesos chilenos. "
                f"Hora de mercado reportada: {market_timestamp} (UTC). "
                "La cotización puede tener retraso y no equivale necesariamente al precio que aplicará un banco o una casa de cambio."
                f"\n\nFuentes consultadas:\n- {evidence.provider}: {evidence.source_url}"
            )
        return (
            f"El dólar observado informado por {evidence.provider} es "
            f"{value} pesos chilenos por dólar para la fecha {data['date']}. "
            "Es un indicador diario, no una cotización intradía en tiempo real."
            f"\n\nFuentes consultadas:\n- {evidence.provider}: {evidence.source_url}"
        )
    if evidence.route == DecisionRoute.SEISMIC:
        return (
            f"El catálogo global de {evidence.provider} registra {data['count']} eventos "
            f"de magnitud 0 o superior entre {data['start']} y {data['end']} (UTC). "
            "Es el conteo publicado por esa fuente para esa ventana; los microsismos por debajo de su umbral no se incluyen."
        )
    if evidence.route == DecisionRoute.WEATHER:
        place = str(data.get("location_name") or "la ubicación consultada")
        condition = str(data.get("condition") or "condición no especificada")
        if data.get("forecast") is True:
            rain = data.get("precipitation_probability_pct")
            rain_text = (
                f" Probabilidad máxima de precipitación: {_format_number_es(rain, decimals=0)}%."
                if isinstance(rain, (int, float)) else ""
            )
            return (
                f"Pronóstico de {evidence.provider} para {place}, {data['forecast_date']}: "
                f"mínima de {_format_number_es(data['temperature_min_c'], decimals=1)} °C, "
                f"máxima de {_format_number_es(data['temperature_max_c'], decimals=1)} °C; "
                f"{condition}.{rain_text} Es un pronóstico, no una medición ni una garantía."
            )
        temperature = _format_number_es(data["temperature_c"], decimals=1)
        when = f" a las {data['time']} ({data.get('timezone') or 'zona horaria del proveedor'})" if data.get("time") else ""
        extra = []
        if isinstance(data.get("feels_like_c"), (int, float)):
            extra.append(f"sensación térmica {_format_number_es(data['feels_like_c'], decimals=1)} °C")
        if isinstance(data.get("relative_humidity_pct"), (int, float)):
            extra.append(f"humedad {_format_number_es(data['relative_humidity_pct'], decimals=0)}%")
        suffix = f" Además: {', '.join(extra)}." if extra else ""
        return (
            f"En {place}, {evidence.provider} reporta {temperature} °C{when}, con {condition}."
            f"{suffix}"
        )
    return "Los datos estructurados se validaron correctamente, pero no hay un formato de respuesta específico para esta ruta."



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
            return await asyncio.shield(task)
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
            key = DecisionCache.key(
                DecisionRoute.WEATHER,
                query,
                str(messages[-1] if messages else ""),
            )
            cached = await self.cache.get(key)
            if cached is not None:
                validated = await validate_evidence(cached)
                if validated.ok:
                    return decision, validated

            data = weather
            if data is None and weather_resolver and messages is not None:
                evidence = await self._singleflight(
                    key,
                    lambda: self._weather_operation(messages, weather_resolver, weather_builder),
                )
                evidence = await validate_evidence(evidence)
                if evidence.ok:
                    await self.cache.put(key, evidence, decision.cache_ttl_seconds)
                return decision, evidence
            if data is None:
                return decision, Evidence(False, DecisionRoute.WEATHER, error="WEATHER_DATA_UNAVAILABLE")

            evidence = Evidence(
                ok=True,
                route=DecisionRoute.WEATHER,
                provider=str(data.get("provider") or "Open-Meteo"),
                source_url=str(data.get("source_url") or "https://api.open-meteo.com/v1/forecast"),
                retrieved_at=_iso_now(),
                data=data,
            )
            evidence = await validate_evidence(evidence)
            if evidence.ok:
                await self.cache.put(key, evidence, decision.cache_ttl_seconds)
            return decision, evidence
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
        try:
            data = await asyncio.wait_for(resolver(messages), timeout=2.5)
        except Exception as exc:
            return Evidence(
                False,
                DecisionRoute.WEATHER,
                error=f"WEATHER_PROVIDER_FAILED:{type(exc).__name__}",
            )
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
