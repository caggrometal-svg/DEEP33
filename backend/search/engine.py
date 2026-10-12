from __future__ import annotations

import asyncio
import os
import re
from functools import lru_cache
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlparse

from backend.search.resilience import DiagnosticCode, controller


ENGINE_VERSION = "1.3.0"
TOKEN_RE = re.compile(r"[\wáéíóúüñÁÉÍÓÚÜÑ]{2,}", re.UNICODE)
DEEP_TERMS = (
    "investiga", "investigar", "investigación", "analiza", "analizar",
    "compara", "contrasta", "verifica", "verificar", "evidencia",
    "fuentes", "en profundidad", "deep", "research", "fact check",
    "fact-check", "conspiración", "conspirativa", "encubrimiento",
    "versión oficial", "narrativa oficial", "anomalía", "hipótesis alternativa",
    "intereses", "manipulación", "contradicciones",
)
QUERY_VARIANTS = ("fuente oficial", "evidencia independiente")

REALTIME_TERMS = (
    "noticia", "noticias", "última hora", "ultima hora", "actualidad", "actual",
    "actualmente", "ahora", "ahora mismo", "hoy", "ayer", "esta semana", "esta noche",
    "último", "últimos", "última", "últimas", "reciente", "recientes", "en desarrollo",
    "qué pasó", "que paso", "qué está pasando", "que esta pasando", "qué ocurre", "que ocurre",
    "clima", "tiempo", "temperatura", "pronóstico", "pronostico", "lluvia", "llover",
    "política", "politica", "presidente", "primer ministro", "elecciones", "elección",
    "gobierno", "congreso", "senado", "ministro", "mercado", "bolsa", "dólar", "dolar",
    "euro", "tipo de cambio", "precio", "cotización", "cotizacion",
    "partido", "resultados", "marcador", "clasificación", "horario", "tráfico", "trafico",
    "vuelo", "vuelos", "alerta", "terremoto", "tsunami", "incendio", "guerra",
)

REALTIME_NEWS_TERMS = (
    "noticia", "noticias", "última hora", "ultima hora", "actualidad", "actual",
    "situación actual", "situacion actual", "estado actual",
    "en desarrollo", "qué pasó", "que paso", "qué está pasando", "que esta pasando",
    "qué ocurre", "que ocurre", "qué pasa", "que pasa", "emergencia", "incidente",
    "incidentes", "contingencia", "contingencias", "suceso", "sucesos",
    "ocurre", "ocurriendo", "sucede", "sucediendo", "alerta", "afectado", "afectada",
    "guerra",
)

CONTROVERSIAL_TERMS = (
    "conspiración", "conspirativa", "encubrimiento", "ocultan", "ocultaron",
    "versión oficial", "narrativa oficial", "comunicado oficial", "evidencia independiente",
    "contradicción", "contradicciones", "anomalía", "anomalías", "agenda", "intereses",
    "manipulación", "fraude", "engaño", "desinformación", "hipótesis alternativa",
    "es verdad", "es cierto", "hay pruebas", "realmente ocurrió", "realmente pasó",
)


_STOPWORDS = {
    "para", "como", "que", "qué", "una", "uno", "los", "las", "del", "de", "la", "al",
    "el", "a", "y", "e", "o", "u", "se", "su", "sus", "mi", "tu", "lo", "le", "les",
    "es", "con", "por", "en", "sobre", "the", "and", "for", "with", "from", "this", "that",
}

_REALTIME_QUERY_FILLERS = {
    # Currentness, dates, and request-format wording are not topic anchors.
    "busca", "buscar", "buscando", "una", "uno", "un",
    "publicada", "publicado", "publicadas", "publicados",
    "publicación", "publicacion", "publicaciones", "fuente", "fuentes",
    "real", "reales", "actualizada", "actualizado", "actualizadas", "actualizados",
    "oficial", "oficiales", "fiable", "fiables", "verificada", "verificado",
    "titular", "titulares", "fecha", "resumen", "breve", "enlace", "enlaces",
    "original", "artículo", "articulo", "artículos", "articulos",
    "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
    "septiembre", "setiembre", "octubre", "noviembre", "diciembre",
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december",
    "situación", "situacion", "actual", "actualmente", "ahora", "mismo", "hoy", "ayer",
    "último", "últimos", "última", "últimas", "ultimo", "ultimos", "ultima", "ultimas",
    "noticia", "noticias", "actualidad", "reciente", "recientes", "información", "informacion",
    "actualización", "actualizacion", "novedad", "novedades", "desarrollo", "estado",
    "pasa", "pasando", "ocurre", "ocurriendo", "sucede", "sucediendo", "en", "vivo",
    "chile", "comuna", "comunas", "municipio", "municipios", "municipalidad",
    "municipalidades", "región", "region", "regiones", "mundial", "mundiales",
    "internacional", "internacionales", "global", "globales", "mundo", "world",
    "worldwide", "news", "breaking", "latest", "las", "los", "del", "de", "la", "al",
}

def _realtime_anchor_tokens(query: str) -> set[str]:
    # Rank topic entities, not prompt instructions or literal publication dates.
    lookup_query = _normalise_lookup_query(query) or str(query or "")
    lookup_query = re.sub(
        r"^(?:busca|buscar|consulta|consultar|investiga|investigar)\s+",
        "",
        lookup_query,
        flags=re.IGNORECASE,
    )
    tokens = _tokens(lookup_query)
    anchors = {
        token for token in tokens
        if token not in _REALTIME_QUERY_FILLERS
        and len(token) >= 3
        and not token.isdigit()
    }
    if anchors:
        return anchors
    # For a broad country-level current-news request, keep country as a fallback
    # anchor. Wikipedia is filtered separately so static pages cannot satisfy it.
    return {token for token in tokens if token in {"chile"}}


def _extract_realtime_locality(query: str) -> str:
    match = re.search(
        r"\b(?:comunas?|municipios?|municipalidades?|ciudades?|localidades?|barrios?|sectores?)"
        r"\s+(?:de|del)\s+(.+?)(?:\s+en\s+chile\b|[,;.!?]|$)",
        query,
        flags=re.IGNORECASE,
    )
    return " ".join(match.group(1).strip().split()) if match else ""
_TRUSTED_SUFFIXES = {".gov": 1.0, ".edu": 0.95, ".org": 0.80}
MIN_FALLBACK_RESULTS = max(1, min(5, int(os.getenv("WEB_SEARCH_MIN_FALLBACK_RESULTS", "2"))))
REALTIME_CORROBORATION_WINDOW_SECONDS = max(
    0.0,
    min(0.5, float(os.getenv("WEB_SEARCH_REALTIME_CORROBORATION_WINDOW_SECONDS", "0.20"))),
)
PROVIDER_RETRIES = max(0, min(1, int(os.getenv("WEB_SEARCH_PROVIDER_RETRIES", "0"))))
RETRY_BACKOFF_SECONDS = max(0.0, min(1.0, float(os.getenv("WEB_SEARCH_RETRY_BACKOFF_SECONDS", "0.25"))))
PARALLEL_PROVIDERS = os.getenv("WEB_SEARCH_PARALLEL_PROVIDERS", "true").strip().lower() == "true"
PARALLEL_QUERIES = os.getenv("WEB_SEARCH_PARALLEL_QUERIES", "true").strip().lower() == "true"
MAX_QUERY_FANOUT = max(1, min(2, int(os.getenv("WEB_SEARCH_MAX_QUERY_FANOUT", "2"))))
MIN_VERIFIED_RESULTS = max(2, min(3, int(os.getenv("WEB_SEARCH_MIN_VERIFIED_RESULTS", "2"))))
TOTAL_SEARCH_TIMEOUT_SECONDS = max(
    3.0,
    min(5.0, float(os.getenv("WEB_SEARCH_TOTAL_TIMEOUT_SECONDS", "5"))),
)


@dataclass(frozen=True)
class SearchPlan:
    original_query: str
    queries: list[str]
    depth: str


def _normalise_lookup_query(query: str) -> str:
    """Keep the information request and remove response-format instructions."""
    cleaned = " ".join(str(query or "").split()).strip()
    cleaned = re.sub(
        r"^(?:busca|buscar|consulta|consultar)\s+"
        r"(?:(?:en\s+)?(?:internet|la\s+web)\s*)?[:,-]?\s*",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    # Search engines need the topic, not the user's full operating instructions.
    # Trim instruction clauses even when the prompt is longer than provider limits.
    instruction_clause = (
        r"(?<=[.!?;:])\s+(?:devuelve|entrega|proporciona|indica|comprueba|verifica|confirma|"
        r"incluye|resume|cita|describe|presenta|informa|muestra|señala|"
        r"no inventes|no afirmes|no supongas|responde en|responde una sola vez|"
        r"usa\s+(?:una\s+)?fuentes?\s+(?:real|reales|actualizada|actualizado|oficial|fiable|verificada)|"
        r"use\s+(?:a\s+)?(?:real|current|official)\s+source|"
        r"formato de salida|resultado de cada prueba|"
        r"si la búsqueda (?:falla|no funciona|no devuelve)|"
        r"si la busqueda (?:falla|no funciona|no devuelve)|"
        r"and (?:return|provide|include|summarize|verify|confirm)|"
        r"do not invent|do not claim|if the search fails)\b.*$"
    )
    cleaned = re.sub(instruction_clause, "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(
        r"\s+(?:y|e)\s+(?:responde|contesta|di|dime)\b.*$",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"\s+(?:responde|contesta)\s+(?:solo|únicamente|unicamente)\b.*$",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    return cleaned.strip(" ?¡!.,;:")

def is_realtime_query(query: str) -> bool:
    """Return True when answering from model memory could be materially stale."""
    lowered = " ".join(str(query or "").split()).strip().lower()
    if not lowered:
        return False
    historical = bool(re.search(r"\b(?:1[5-9]\d{2}|20(?:0\d|1\d))\b", lowered))
    current_marker = any(
        marker in lowered
        for marker in ("hoy", "ahora", "actual", "actualmente", "último", "última", "últimos", "últimas", "en vivo", "live", "breaking", "latest", "current", "today")
    )
    if historical and not current_marker:
        return False
    if any(term in lowered for term in REALTIME_TERMS):
        return True

    # Natural weather questions can omit explicit words such as "clima".
    weather_semantic = bool(re.search(
        r"\b(?:hara|hará|estara|estará|como estará|como estara|qué tan|que tan|qué tal|que tal)"
        r"[^.?!]{0,80}\b(?:calor|frío|frio|helado|helada)\b",
        lowered,
    ))
    weather_context = any(term in lowered for term in (
        "cómo estará el día", "como estara el dia",
        "cómo estará mañana", "como estara manana",
        "cómo estará hoy", "como estara hoy",
        "hará calor", "hara calor", "hará frío", "hara frio",
        "qué tan frío", "que tan frio", "qué tan caluroso", "que tan caluroso",
    ))
    if weather_semantic or weather_context:
        return True
    return bool(re.search(
        r"\b(?:2026|fecha|día|hora|vigente|en vivo|live|breaking|latest|current|today|yesterday|"
        r"president|election|weather|climate|temperature|news|politics|stock|exchange rate)\b",
        lowered,
    ))


def _realtime_query_variants(lookup_query: str, lowered: str, local_date: str) -> list[str]:
    variants: list[str] = []
    is_news = any(term in lowered for term in REALTIME_NEWS_TERMS)
    locality = _extract_realtime_locality(lookup_query)
    if is_news and locality:
        variants.append(f"{locality} Chile últimas noticias de hoy {local_date}".strip())
        variants.append(f"{locality} Chile actualidad alertas de hoy {local_date}".strip())
    elif is_news:
        variants.append(f"{lookup_query} últimas noticias de hoy {local_date}".strip())
        variants.append(f"{lookup_query} última hora y actualización {local_date}".strip())
    elif any(term in lowered for term in ("clima", "tiempo", "temperatura", "pronóstico", "pronostico")):
        weather_base = _normalise_lookup_query(lookup_query) or lookup_query
        variants.append(f"{weather_base} temperatura humedad lluvia condiciones actuales hoy {local_date}".strip())
        variants.append(
            f"site:meteochile.gob.cl {weather_base} temperatura pronóstico {local_date}".strip()
        )
    elif any(term in lowered for term in ("política", "politica", "presidente", "elecciones", "elección", "gobierno", "congreso", "senado", "ministro")):
        variants.append(f"{lookup_query} actualidad política hoy {local_date}".strip())
        variants.append(f"{lookup_query} últimas novedades y cambios {local_date}".strip())
    else:
        variants.append(f"{lookup_query} actualización de hoy {local_date}".strip())
        variants.append(f"{lookup_query} información más reciente {local_date}".strip())
    return variants


@lru_cache(maxsize=4096)
def _cached_tokens(text: str) -> tuple[str, ...]:
    return tuple(
        token.lower()
        for token in TOKEN_RE.findall(text)
        if token.lower() not in _STOPWORDS
    )


def _tokens(text: str) -> list[str]:
    return list(_cached_tokens(str(text)))


@lru_cache(maxsize=4096)
def _token_set(text: str) -> frozenset[str]:
    return frozenset(_cached_tokens(str(text)))


_TOPIC_FILLERS = {
    "official", "officially", "documentation", "document", "docs",
    "fact", "facts", "sheet", "sheets", "text", "website", "webpage",
    "page", "pages", "source", "sources", "site", "search", "internet",
    "online", "what", "which", "when", "where", "why", "how",
    "whoever", "best", "top", "latest", "current", "currently",
    "today", "now", "information", "info", "guide", "overview",
    "investiga", "investigar", "investigación", "investigacion",
    "investigate", "analiza", "analizar", "analyze", "analyse",
    "compara", "comparar", "compare", "contrasta", "contrastar",
    "contrast", "consulta", "consultar", "rápida", "rapida", "rápido",
    "rapido", "quick", "fast", "deep", "profundidad",
    "please", "tell", "show", "explain", "find", "about",
    "oficial", "oficiales", "documentación", "documentacion",
    "documento", "documentos", "ficha", "fichas", "texto", "sitio",
    "página", "pagina", "páginas", "paginas", "fuente", "fuentes",
    "buscar", "busca", "internet", "web", "información", "informacion",
    "guía", "guia", "resumen", "sobre", "cuándo", "cuando", "dónde",
    "donde", "porqué", "porque", "cómo", "como", "mejor", "mejores",
}
_TOPIC_PHRASE_ALIASES = (
    (re.compile(r"\bworld\s+health\s+organization\b", re.IGNORECASE), "who"),
    (re.compile(r"\bnational\s+aeronautics\s+and\s+space\s+administration\b", re.IGNORECASE), "nasa"),
)


def _topic_tokens(query: str) -> list[str]:
    normalized = _normalise_lookup_query(query)
    for pattern, replacement in _TOPIC_PHRASE_ALIASES:
        normalized = pattern.sub(replacement, normalized)
    ignored = _TOPIC_FILLERS | _REALTIME_QUERY_FILLERS
    return [
        token
        for token in _tokens(normalized)
        if token not in ignored and not token.isdigit()
    ]


def _canonical_url(url: str) -> str:
    parsed = urlparse(url.strip())
    path = parsed.path.rstrip("/") or "/"
    return parsed._replace(fragment="", path=path).geturl()


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower().rstrip(".")


def _domain_quality(url: str) -> float:
    host = _host(url)
    if not host:
        return 0.0
    exact = {
        "reuters.com": 0.95,
        "apnews.com": 0.95,
        "bbc.com": 0.90,
        "bbc.co.uk": 0.90,
        "nasa.gov": 1.0,
        "who.int": 1.0,
        "un.org": 1.0,
        "meteochile.gob.cl": 1.0,
    }
    if host in exact:
        return exact[host]
    for suffix, score in _TRUSTED_SUFFIXES.items():
        if host.endswith(suffix) or f"{suffix}." in host:
            return score
    return 0.55


def _evidence_text(item: dict) -> str:
    return f'{item.get("title", "")} {item.get("snippet", "")}'.lower()


def _similarity(left: dict, right: dict) -> float:
    a = _token_set(_evidence_text(left))
    b = _token_set(_evidence_text(right))
    if not a or not b:
        return 0.0
    return len(a & b) / max(1, len(a | b))


def _corroboration_counts(results: list[dict]) -> dict[int, int]:
    counts = {id(item): 0 for item in results}
    prepared = [
        (_host(str(item.get("url") or "")), _token_set(_evidence_text(item)))
        for item in results
    ]
    for index, (left_host, left_tokens) in enumerate(prepared):
        if not left_host or not left_tokens:
            continue
        for right_index in range(index + 1, len(prepared)):
            right_host, right_tokens = prepared[right_index]
            if not right_host or not right_tokens or left_host == right_host:
                continue
            similarity = len(left_tokens & right_tokens) / max(
                1, len(left_tokens | right_tokens)
            )
            if similarity >= 0.25:
                counts[id(results[index])] += 1
                counts[id(results[right_index])] += 1
    return counts


def _freshness_score(item: dict) -> float:
    value = str(item.get("published_at") or "").strip()
    if not value:
        return 0.0
    try:
        published = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        age_days = max(
            0.0,
            (datetime.now(timezone.utc) - published.astimezone(timezone.utc)).total_seconds() / 86400,
        )
        return max(0.0, 1.0 - min(age_days / 365.0, 1.0))
    except ValueError:
        return 0.0


def rank_results(
    query: str,
    results: list[dict],
    *,
    realtime: bool = False,
) -> list[dict]:
    query_tokens = _tokens(query)
    topic_tokens = _topic_tokens(query)
    anchor_tokens = _realtime_anchor_tokens(query) if realtime else set()
    support = _corroboration_counts(results)
    ranked = []
    for item in results:
        url = str(item.get("url") or "").strip()
        if not url:
            continue
        if realtime and _host(url).endswith("wikipedia.org"):
            # Static encyclopedia content is not live/current evidence.
            continue
        if realtime and re.search(
            r"\s[-–|]\s*(?:facebook|instagram|tiktok|reddit|youtube)\s*$",
            str(item.get("title") or ""),
            re.IGNORECASE,
        ):
            # Google News can wrap social-media reposts in RSS article URLs;
            # they are not independent newsroom sources for a current-news answer.
            continue
        parsed_url = urlparse(url)
        anchor_evidence = (
            _evidence_text(item)
            + " "
            + (parsed_url.hostname or "")
            + " "
            + parsed_url.path.replace("-", " ").replace("_", " ").replace("/", " ")
        )
        evidence_tokens = _token_set(anchor_evidence)
        anchor_hits = len(anchor_tokens & evidence_tokens)
        required_anchor_hits = min(2, len(anchor_tokens))
        if realtime and anchor_tokens and anchor_hits < required_anchor_hits:
            # Country/currentness words alone do not prove relevance to a named locality.
            continue
        topic_evidence = anchor_evidence
        for pattern, replacement in _TOPIC_PHRASE_ALIASES:
            topic_evidence = pattern.sub(replacement, topic_evidence)
        topic_evidence_tokens = _token_set(topic_evidence)
        topic_hits = len(set(topic_tokens) & topic_evidence_tokens)
        required_topic_hits = min(2, len(topic_tokens))
        if not realtime and topic_tokens and topic_hits < required_topic_hits:
            # Domain reputation and corroboration must never make unrelated results relevant.
            continue
        relevance = max(
            _text_score(query_tokens, item),
            _text_score(topic_tokens, item) if topic_tokens else 0.0,
        )
        anchor_score = (
            min(1.0, anchor_hits / max(1, min(2, len(anchor_tokens))))
            if anchor_tokens
            else 0.0
        )
        quality = _domain_quality(url)
        freshness = _freshness_score(item)
        corroboration = min(1.0, support.get(id(item), 0) / 2.0)
        if realtime:
            score = (
                relevance * 0.42
                + anchor_score * 0.28
                + quality * 0.15
                + freshness * 0.05
                + corroboration * 0.10
            )
        else:
            score = relevance * 0.50 + quality * 0.20 + freshness * 0.10 + corroboration * 0.20
        ranked.append({
            **item,
            "score": round(score, 6),
            "corroboration_count": support.get(id(item), 0),
            "corroborated": support.get(id(item), 0) >= 1,
            "anchor_hits": anchor_hits,
            "source_domain": _host(url),
        })
    ranked.sort(
        key=lambda item: (
            item.get("score", 0.0),
            item.get("corroboration_count", 0),
            str(item.get("title", "")).lower(),
        ),
        reverse=True,
    )
    return ranked


def _text_score(query_tokens: list[str], item: dict) -> float:
    title = str(item.get("title") or "").lower()
    snippet = str(item.get("snippet") or "").lower()
    if not query_tokens:
        return 0.0
    title_hits = sum(1 for token in query_tokens if token in title)
    snippet_hits = sum(1 for token in query_tokens if token in snippet)
    return (title_hits / len(query_tokens)) * 0.70 + (snippet_hits / len(query_tokens)) * 0.30


def deduplicate(results: list[dict], limit: int) -> list[dict]:
    seen_urls = set()
    seen_title_domain = set()
    seen_domains = set()
    output = []

    # Pass 1: guarantee source-domain diversity where possible. Identical titles
    # from different domains are retained because they are evidence of corroboration.
    for item in results:
        url = _canonical_url(str(item.get("url") or ""))
        title = " ".join(str(item.get("title") or "").lower().split())
        domain = _host(url)
        title_key = (domain, title) if title else None
        if not url or url in seen_urls or (title_key and title_key in seen_title_domain):
            continue
        if domain and domain in seen_domains:
            continue
        seen_urls.add(url)
        if title_key:
            seen_title_domain.add(title_key)
        if domain:
            seen_domains.add(domain)
        output.append({**item, "url": url})
        if len(output) >= limit:
            return output

    # Pass 2: fill remaining slots, capped by two results per domain.
    domain_counts: dict[str, int] = {}
    for item in output:
        domain = _host(item["url"])
        domain_counts[domain] = domain_counts.get(domain, 0) + 1

    for item in results:
        url = _canonical_url(str(item.get("url") or ""))
        title = " ".join(str(item.get("title") or "").lower().split())
        domain = _host(url)
        title_key = (domain, title) if title else None
        if not url or url in seen_urls or (title_key and title_key in seen_title_domain):
            continue
        if domain and domain_counts.get(domain, 0) >= 2:
            continue
        seen_urls.add(url)
        if title_key:
            seen_title_domain.add(title_key)
        domain_counts[domain] = domain_counts.get(domain, 0) + 1
        output.append({**item, "url": url})
        if len(output) >= limit:
            break
    return output


class SearchEngine:
    """Provider-agnostic DEEP33 web-search orchestration layer."""

    def __init__(self, *, max_results: int = 5, max_queries: int = 3) -> None:
        self.max_results = max(1, min(8, int(max_results)))
        self.max_queries = max(1, min(3, int(max_queries)))

    def plan(self, query: str) -> SearchPlan:
        cleaned = " ".join(query.split()).strip()
        lookup_query = _normalise_lookup_query(cleaned) or cleaned
        lowered = cleaned.lower()
        exact_deep = bool(re.search(r"\bdeep\b", lowered))
        deep_signal = exact_deep or any(
            term != "deep" and term in lowered for term in DEEP_TERMS
        )
        realtime_signal = is_realtime_query(cleaned)
        if realtime_signal:
            depth = "realtime"
        elif deep_signal:
            depth = "deep"
        else:
            depth = "standard"
        queries = [lookup_query]
        if depth == "realtime":
            local_date = datetime.now(timezone.utc).date().isoformat()
            for candidate in _realtime_query_variants(lookup_query, lowered, local_date):
                if candidate.lower() != lookup_query.lower() and len(queries) < self.max_queries:
                    queries.append(candidate)
        elif depth == "deep":
            controversial = any(term in lowered for term in CONTROVERSIAL_TERMS)
            variant_pool = ("versión oficial", "evidencia independiente") if controversial else QUERY_VARIANTS
            for variant in variant_pool:
                candidate = f"{lookup_query} {variant}".strip()
                if candidate.lower() != lookup_query.lower() and len(queries) < self.max_queries:
                    queries.append(candidate)
        return SearchPlan(original_query=cleaned, queries=queries, depth=depth)

    async def _provider_search(
        self,
        provider: str,
        query: str,
        *,
        timeout_seconds: float,
        api_key: str,
    ) -> list[dict]:
        from tools.web_search import _bing_search, _duckduckgo_search, _google_news_search, _mojeek_search, _tavily_search

        if provider == "tavily":
            if not api_key:
                return []
            return await _tavily_search(query, api_key, timeout_seconds, self.max_results)
        if provider == "google_news":
            return await _google_news_search(query, timeout_seconds, self.max_results)
        if provider == "bing":
            return await _bing_search(query, timeout_seconds, self.max_results)
        if provider == "mojeek":
            return await _mojeek_search(query, timeout_seconds, self.max_results)
        return await _duckduckgo_search(query, timeout_seconds, self.max_results)

    async def _provider_search_with_retry(
        self,
        provider: str,
        query: str,
        *,
        timeout_seconds: float,
        api_key: str,
        fast_mode: bool = False,
    ) -> tuple[list[dict], list[str]]:
        errors: list[str] = []
        max_attempts = 1 if fast_mode else PROVIDER_RETRIES + 1
        for attempt in range(max_attempts):
            try:
                result = await asyncio.wait_for(
                    self._provider_search(
                        provider,
                        query,
                        timeout_seconds=timeout_seconds,
                        api_key=api_key,
                    ),
                    timeout=max(1.0, float(timeout_seconds) + 0.5),
                )
                return result, errors
            except Exception as exc:
                errors.append(f"{provider}:{type(exc).__name__}:attempt={attempt + 1}")
                if (
                    not fast_mode
                    and attempt < max_attempts - 1
                    and RETRY_BACKOFF_SECONDS
                ):
                    await asyncio.sleep(RETRY_BACKOFF_SECONDS * (attempt + 1))
        return [], errors

    def _providers(self, provider_name: str, api_key: str, fallback_ddg: bool) -> list[str]:
        bing_enabled = os.getenv("WEB_SEARCH_BING_ENABLED", "true").strip().lower() == "true"
        mojeek_enabled = os.getenv("WEB_SEARCH_MOJEEK_ENABLED", "true").strip().lower() == "true"
        if provider_name == "tavily":
            providers = ["tavily"]
            if bing_enabled:
                providers.append("bing")
            if mojeek_enabled:
                providers.append("mojeek")
            if fallback_ddg:
                providers.append("duckduckgo")
            return providers
        if provider_name == "bing":
            providers = ["bing"]
            if mojeek_enabled:
                providers.append("mojeek")
            if fallback_ddg:
                providers.append("duckduckgo")
            return providers
        if provider_name == "mojeek":
            return ["mojeek"]
        if provider_name == "duckduckgo":
            return ["duckduckgo"]
        providers: list[str] = []
        if api_key:
            providers.append("tavily")
        if bing_enabled:
            providers.append("bing")
        if mojeek_enabled:
            providers.append("mojeek")
        if fallback_ddg or not providers:
            providers.append("duckduckgo")
        return providers

    async def _run_query(
        self,
        providers: list[str],
        planned_query: str,
        *,
        plan_depth: str,
        timeout_seconds: float,
        api_key: str,
        fast_mode: bool = False,
    ) -> tuple[list[tuple[str, list[dict]]], list[str], list[str]]:
        """Run independent providers with a strict staged failover budget.

        T0 starts the configured primary providers. DuckDuckGo is held back as an
        independent fallback until T+2s unless the primary stage already produced
        sufficient relevant evidence. Every created task is cancelled and drained.
        """
        successful: list[tuple[str, list[dict]]] = []
        errors: list[str] = []
        attempted: list[str] = []
        required_coverage = 1 if fast_mode or plan_depth == "realtime" else min(
            self.max_results, MIN_FALLBACK_RESULTS
        )

        def relevant(items: list[dict]) -> list[dict]:
            return rank_results(
                planned_query,
                items,
                realtime=plan_depth == "realtime",
            )

        def enough() -> bool:
            combined = [item for _provider, batch in successful for item in batch]
            ranked = relevant(combined)
            urls = {
                _canonical_url(str(item.get("url") or ""))
                for item in ranked
                if str(item.get("url") or "").strip()
            }
            domains = {_host(url) for url in urls if _host(url)}
            if fast_mode or plan_depth == "realtime":
                return bool(ranked)
            if plan_depth == "deep" and len(providers) > 1:
                return len({name for name, _batch in successful}) >= 2 and len(urls) >= min(3, self.max_results)
            return len(urls) >= required_coverage and len(domains) >= min(required_coverage, 2)

        async def call(name: str) -> tuple[str, list[dict], list[str]]:
            async def operation() -> list[dict]:
                return await self._provider_search(
                    name,
                    planned_query,
                    timeout_seconds=min(
                        float(timeout_seconds),
                        controller.budgets.provider_total_seconds,
                    ),
                    api_key=api_key,
                )

            results, attempt = await controller.run_provider(
                name,
                operation,
                timeout_seconds=min(
                    float(timeout_seconds),
                    controller.budgets.provider_total_seconds,
                ),
            )
            provider_errors: list[str] = []
            if not attempt.ok:
                provider_errors.append(
                    f"{name}:{attempt.code}:elapsed_ms={attempt.elapsed_ms:.2f}"
                )
            return name, results, provider_errors

        async def collect(tasks: list[asyncio.Task], timeout: float | None) -> list[asyncio.Task]:
            if not tasks:
                return []
            done, pending = await asyncio.wait(
                tasks,
                timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
            pending_list = list(pending)
            for task in done:
                try:
                    name, results, provider_errors = task.result()
                except asyncio.CancelledError:
                    continue
                except Exception as exc:
                    errors.append(f"provider_task:{type(exc).__name__}")
                    continue
                if name and name not in attempted:
                    attempted.append(name)
                errors.extend(provider_errors)
                ranked = relevant(results)
                if ranked:
                    successful.append((name, results))
                elif results:
                    errors.append(f"{name}:no_relevant_results:{len(results)}")
                elif not provider_errors:
                    errors.append(f"{name}:{DiagnosticCode.NO_RESULTS}")
            return pending_list

        # Preserve explicitly configured premium/news providers at T0, while the
        # public general-web path uses Bing + Mojeek first and DDG as delayed fallback.
        primary_names = [name for name in providers if name != "duckduckgo"]
        fallback_names = [name for name in providers if name == "duckduckgo"]
        if not primary_names and fallback_names:
            primary_names = fallback_names
            fallback_names = []

        primary_tasks = [
            controller.safe_task(
                call(name),
                name=f"web-search:{name}",
            )
            for name in primary_names
        ]
        all_tasks = list(primary_tasks)
        try:
            # T0 -> T+2: accept early useful evidence without waiting for dead peers.
            deadline = asyncio.get_running_loop().time() + max(
                0.1,
                min(float(timeout_seconds), controller.budgets.provider_total_seconds),
            )
            staged_until = min(
                deadline,
                asyncio.get_running_loop().time() + controller.budgets.fallback_delay_seconds,
            )
            pending = list(primary_tasks)
            while pending and asyncio.get_running_loop().time() < staged_until and not enough():
                remaining = max(0.0, staged_until - asyncio.get_running_loop().time())
                pending = await collect(pending, remaining)

            if enough():
                return successful, errors, attempted

            fallback_tasks = [
                controller.safe_task(
                    call(name),
                    name=f"web-search:{name}",
                )
                for name in fallback_names
            ]
            all_tasks.extend(fallback_tasks)
            pending.extend(fallback_tasks)

            # Continue within the strict provider/global caller budget.
            while pending and asyncio.get_running_loop().time() < deadline and not enough():
                remaining = max(0.0, deadline - asyncio.get_running_loop().time())
                pending = await collect(pending, remaining)

            if not successful and asyncio.get_running_loop().time() >= deadline:
                errors.append("search:deadline")
            return successful, errors, attempted
        finally:
            await controller.cancel_and_drain(all_tasks)

    async def search(
        self,
        query: str,
        *,
        provider: str,
        api_key: str,
        timeout_seconds: float,
        fallback_ddg: bool,
        fast_mode: bool = False,
    ) -> dict:
        plan = self.plan(query)
        provider_name = provider.strip().lower() or "auto"
        providers = self._providers(provider_name, api_key, fallback_ddg)
        normalized_query = " ".join(str(query or "").split()).lower()
        if plan.depth == "realtime" and any(term in normalized_query for term in REALTIME_NEWS_TERMS):
            if "google_news" not in providers:
                providers.insert(0, "google_news")

        successful: list[tuple[str, str, list[dict]]] = []
        errors: list[str] = []
        providers_attempted: list[str] = []

        planned_queries = plan.queries[:1] if fast_mode else plan.queries

        search_deadline = asyncio.get_running_loop().time() + TOTAL_SEARCH_TIMEOUT_SECONDS

        async def run_planned(planned_query: str):
            remaining = search_deadline - asyncio.get_running_loop().time()
            if remaining <= 0.25:
                return planned_query, ([], ["search:deadline"], [])
            try:
                batch = await asyncio.wait_for(
                    self._run_query(
                        providers,
                        planned_query,
                        plan_depth=plan.depth,
                        timeout_seconds=min(timeout_seconds, max(1.0, remaining)),
                        api_key=api_key,
                        fast_mode=fast_mode,
                    ),
                    timeout=remaining,
                )
                return planned_query, batch
            except asyncio.TimeoutError:
                return planned_query, ([], ["search:deadline"], [])

        if (
            not fast_mode
            and plan.depth in {"deep", "realtime"}
            and len(planned_queries) > 1
        ):
            # First query is authoritative. Expand only when the initial result
            # set is not sufficiently verified, instead of always running all
            # variants in parallel.
            first_batch = await run_planned(planned_queries[0])
            planned_batches = [first_batch]
            first_results, _first_errors, _first_attempted = first_batch[1]
            first_relevant_by_provider = [
                (
                    name,
                    rank_results(
                        plan.original_query,
                        items,
                        realtime=plan.depth == "realtime",
                    ),
                )
                for name, items in first_results
            ]
            first_relevant_by_provider = [
                (name, items) for name, items in first_relevant_by_provider if items
            ]
            first_provider_count = len(first_relevant_by_provider)
            first_result_count = sum(
                len(items) for _name, items in first_relevant_by_provider
            )
            required_results = (
                min(3, self.max_results)
                if plan.depth == "deep"
                else min(MIN_VERIFIED_RESULTS, self.max_results)
            )
            if plan.depth == "realtime":
                # _run_query already races providers and gives them a short
                # corroboration window. Any relevant result can answer a broad
                # current-news request without launching another query variant.
                sufficiently_verified = first_provider_count >= 1 and first_result_count >= 1
            else:
                min_provider_successes = 1 if len(providers) == 1 else 2
                sufficiently_verified = (
                    first_provider_count >= min_provider_successes
                    and first_result_count >= required_results
                )
            if not sufficiently_verified:
                remaining = planned_queries[1:MAX_QUERY_FANOUT]
                if remaining:
                    planned_batches.extend(
                        await asyncio.gather(*(run_planned(q) for q in remaining))
                    )
        else:
            planned_batches = []
            for q in planned_queries:
                planned_batches.append(await run_planned(q))

        for planned_query, (batch, batch_errors, attempted) in planned_batches:
            errors.extend(batch_errors)
            for provider_used in attempted:
                if provider_used not in providers_attempted:
                    providers_attempted.append(provider_used)
            for provider_used, items in batch:
                successful.append((planned_query, provider_used, items))

        executed_queries = [query for query, _batch in planned_batches]
        if not successful:
            locality = _extract_realtime_locality(plan.original_query)
            local_date = datetime.now(timezone.utc).date().isoformat()
            lowered_original = plan.original_query.lower()
            is_news_request = any(term in lowered_original for term in REALTIME_NEWS_TERMS)
            if plan.depth == "realtime" and locality:
                rescue = f"{locality} Chile noticias hoy {local_date}"
            elif (
                plan.depth == "realtime"
                and is_news_request
                and "chile" in _tokens(plan.original_query)
            ):
                rescue = f"Chile noticias hoy {local_date}"
            else:
                rescue_base = _normalise_lookup_query(plan.original_query)
                tokens = [
                    token for token in _tokens(rescue_base)
                    if token not in _REALTIME_QUERY_FILLERS and not token.isdigit()
                ]
                rescue = " ".join(tokens[:-1]).strip() if len(tokens) >= 3 else rescue_base
            # Never record or issue a rescue query after the shared deadline.
            remaining_budget = search_deadline - asyncio.get_running_loop().time()
            if (
                rescue
                and rescue.lower() != plan.original_query.lower()
                and remaining_budget > 0.25
            ):
                executed_queries.append(rescue)
                _rescue_query, rescue_result = await run_planned(rescue)
                batch, batch_errors, attempted = rescue_result
                errors.extend(batch_errors)
                for provider_used in attempted:
                    if provider_used not in providers_attempted:
                        providers_attempted.append(provider_used)
                for provider_used, items in batch:
                    successful.append((rescue, provider_used, items))

        merged = []
        providers_used = []
        for planned_query, name, items in successful:
            if name not in providers_used:
                providers_used.append(name)
            for item in items:
                merged.append({
                    **item,
                    "search_query": planned_query,
                    "provider": name,
                })

        ranked = rank_results(
            plan.original_query,
            merged,
            realtime=plan.depth == "realtime",
        )
        selected = deduplicate(ranked, self.max_results)
        distinct_domains = sorted({
            _host(str(item.get("url") or ""))
            for item in selected
            if _host(str(item.get("url") or ""))
        })
        corroborated = sum(1 for item in selected if item.get("corroborated"))
        if corroborated:
            verification_level = "corroborated"
        elif len(distinct_domains) >= 2:
            verification_level = "multi-source"
        elif selected:
            verification_level = "single-source"
        else:
            verification_level = "none"

        return {
            "ok": bool(selected),
            "realtime": plan.depth == "realtime",
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "engine": "DEEP33 Search Engine",
            "engine_version": ENGINE_VERSION,
            "provider_independent": True,
            "query": plan.original_query,
            "depth": plan.depth,
            "realtime_news": any(
                term in plan.original_query.lower() for term in REALTIME_NEWS_TERMS
            ),
            "queries": list(planned_queries),
            "queries_executed": executed_queries,
            "providers_attempted": providers_attempted,
            "providers": providers_used,
            "provider": providers_used[0] if len(providers_used) == 1 else "multi" if providers_used else None,
            "results": selected,
            "sources": selected,
            "verification": {
                "level": verification_level,
                "distinct_domains": len(distinct_domains),
                "distinct_providers": len(providers_used),
                "corroborated_results": corroborated,
            },
            "errors": errors[:8],
        }
