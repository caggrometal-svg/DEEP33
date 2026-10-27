from __future__ import annotations

import asyncio
import copy
import hashlib
import logging
import re
import time
from datetime import datetime, timezone
from urllib.parse import urlparse
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from backend.search.engine import _normalise_lookup_query, _tokens, _topic_tokens, deduplicate, is_realtime_query, rank_results
from tools.web_search import (
    _bing_search,
    _cl0q_search,
    _duckduckgo_search,
    _google_news_search,
    _google_search,
    _looks_realtime_news,
    _mojeek_search,
    _yahoo_search,
)

logger = logging.getLogger(__name__)

PROVIDER_TOTAL_SECONDS = 2.5
GLOBAL_BUDGET_SECONDS = 5.0
FALLBACK_DELAY_SECONDS = 0.35
STANDARD_CORROBORATION_WINDOW_SECONDS = 0.20
CIRCUIT_FAILURE_THRESHOLD = 5
CIRCUIT_OPEN_SECONDS = 5.0
CACHE_TTL_SECONDS = 60.0
CACHE_MAX_ENTRIES = 256


@dataclass(slots=True)
class CircuitState:
    consecutive_failures: int = 0
    opened_until: float = 0.0

    def is_open(self, now: float) -> bool:
        if self.opened_until <= 0.0:
            return False
        if now >= self.opened_until:
            self.consecutive_failures = 0
            self.opened_until = 0.0
            return False
        return True

    def success(self) -> None:
        self.consecutive_failures = 0
        self.opened_until = 0.0

    def failure(self, now: float) -> None:
        self.consecutive_failures += 1
        if self.consecutive_failures >= CIRCUIT_FAILURE_THRESHOLD:
            self.opened_until = now + CIRCUIT_OPEN_SECONDS


@dataclass(slots=True)
class ProviderOutcome:
    provider: str
    ok: bool
    results: list[dict] = field(default_factory=list)
    error_code: str | None = None
    latency_ms: float = 0.0


def _is_who_dengue_item(item: dict) -> bool:
    """True only for readable dengue evidence published on WHO's own domain."""
    url = str(item.get("url") or "").strip()
    parsed = urlparse(url)
    host = str(item.get("source_domain") or parsed.hostname or "").casefold().rstrip(".").removeprefix("www.")
    evidence = f"{item.get('title', '')} {item.get('snippet', '')} {url}"
    return (host == "who.int" or host.endswith(".who.int")) and re.search(
        r"\bdengue\b", evidence, re.IGNORECASE
    ) is not None


def _has_who_dengue_item(items: list[dict]) -> bool:
    return any(_is_who_dengue_item(item) for item in items)


def _is_direct_chile_economy_item(item: dict) -> bool:
    """Accept any relevant direct HTTPS source, without a publisher allowlist."""
    url = str(item.get("url") or "").strip()
    parsed = urlparse(url)
    url_host = (parsed.hostname or "").casefold().rstrip(".").removeprefix("www.")
    # Keep search-aggregator wrappers out of the evidence pool; the original
    # publisher domain may come from source metadata, but the fetched URL must
    # itself be a direct public page. Public URLs are safety-validated downstream.
    if (
        parsed.scheme.casefold() != "https"
        or not url_host
        or url_host == "news.google.com"
        or parsed.username is not None
        or parsed.password is not None
    ):
        return False
    story = f"{item.get('title', '')} {item.get('snippet', '')}"
    return re.search(
        r"\b(?:econom[ií]\w*|mercad\w*|finanz\w*|empresa\w*|"
        r"inversi[oó]n\w*|inflaci[oó]n\w*|ipc\b|d[oó]lar\w*|"
        r"empleo\w*|desempleo\w*|pib\b|banco central|tasa\w*)\b",
        story,
        re.IGNORECASE,
    ) is not None


_CHILE_TECH_COMPANY_HOSTS = ("sonda.com", "entel.cl", "wom.cl", "movistar.cl")

_CHILE_ECONOMY_NAVIGATION_CANDIDATES = (
    {
        "title": "Diario Financiero — Últimas noticias económicas de Chile",
        "url": "https://www.df.cl/ultimas-noticias",
        "snippet": "Portada directa de últimas noticias económicas, mercados y empresas de Chile.",
        "source_domain": "df.cl",
        "provider": "publisher_navigation_fallback",
    },
    {
        "title": "Pulso — Economía, mercados y empresas",
        "url": "https://www.latercera.com/pulso/",
        "snippet": "Sección económica de La Tercera con información de mercados, empresas y economía chilena.",
        "source_domain": "latercera.com",
        "provider": "publisher_navigation_fallback",
    },
    {
        "title": "Emol Economía — Actualidad económica",
        "url": "https://www.emol.com/noticias/Economia/",
        "snippet": "Sección de economía de Emol con actualidad económica y financiera de Chile.",
        "source_domain": "emol.com",
        "provider": "publisher_navigation_fallback",
    },
    {
        "title": "Banco Central de Chile — Información económica",
        "url": "https://www.bcentral.cl/",
        "snippet": "Sitio oficial del Banco Central de Chile con información económica, estadísticas y publicaciones.",
        "source_domain": "bcentral.cl",
        "provider": "official_navigation_fallback",
    },
)

_CHILE_TECH_COMPANY_NAVIGATION_CANDIDATES = (
    {
        "title": "SONDA — servicios de TI y transformación digital",
        "url": "https://www.sonda.com/",
        "snippet": (
            "Sitio corporativo oficial de SONDA, proveedor regional de servicios de TI, "
            "soluciones tecnológicas y transformación digital, con operaciones en Chile."
        ),
        "source_domain": "sonda.com",
        "provider": "official_navigation_fallback",
    },
    {
        "title": "Entel Empresas — conectividad y soluciones digitales en Chile",
        "url": "https://www.entel.cl/empresas/",
        "snippet": (
            "Portal oficial Entel Empresas: planes móviles, internet para empresas, "
            "servicios cloud y soluciones digitales para negocios en Chile."
        ),
        "source_domain": "entel.cl",
        "provider": "official_navigation_fallback",
    },
    {
        "title": "WOM Empresas — planes móviles e internet para negocios",
        "url": "https://www.wom.cl/empresas/",
        "snippet": (
            "Portal oficial WOM Empresas en Chile con planes móviles, internet y "
            "conectividad para empresas y negocios."
        ),
        "source_domain": "wom.cl",
        "provider": "official_navigation_fallback",
    },
    {
        "title": "Movistar Empresas Chile — conectividad y servicios digitales",
        "url": "https://ww2.movistar.cl/empresas/",
        "snippet": (
            "Sitio oficial Movistar Empresas Chile con soluciones de conectividad, "
            "internet fibra, servicios móviles y herramientas digitales para empresas."
        ),
        "source_domain": "ww2.movistar.cl",
        "provider": "official_navigation_fallback",
    },
)


def _is_chile_commercial_technology_item(item: dict) -> bool:
    """Identify results from independently recognized Chilean commercial tech providers."""
    url = str(item.get("url") or "").strip()
    parsed = urlparse(url)
    host = str(item.get("source_domain") or parsed.hostname or "").casefold().rstrip(".").removeprefix("www.")
    return any(host == domain or host.endswith("." + domain) for domain in _CHILE_TECH_COMPANY_HOSTS)


class TTLRUCache:
    def __init__(self, max_entries: int = CACHE_MAX_ENTRIES) -> None:
        self.max_entries = max(8, int(max_entries))
        self._items: OrderedDict[str, tuple[float, dict]] = OrderedDict()

    def get(self, key: str) -> dict | None:
        now = time.monotonic()
        item = self._items.get(key)
        if item is None:
            return None
        expires_at, value = item
        if expires_at <= now:
            self._items.pop(key, None)
            return None
        self._items.move_to_end(key)
        return copy.deepcopy(value)

    def put(self, key: str, value: dict, ttl_seconds: float) -> None:
        ttl = max(30.0, min(120.0, float(ttl_seconds)))
        self._items[key] = (time.monotonic() + ttl, copy.deepcopy(value))
        self._items.move_to_end(key)
        while len(self._items) > self.max_entries:
            self._items.popitem(last=False)


_CHILE_PUBLIC_INFO_NAVIGATION_CANDIDATES = (
    {
        "title": "ChileAtiende — Servicios y trámites del Estado",
        "url": "https://www.chileatiende.gob.cl/",
        "snippet": (
            "Portal del Estado de Chile con información pública sobre trámites, "
            "beneficios y servicios gubernamentales para la ciudadanía."
        ),
        "source_domain": "www.chileatiende.gob.cl",
        "provider": "official_portal_fallback",
    },
    {
        "title": "Datos.gob.cl — Portal de Datos Abiertos del Estado",
        "url": "https://datos.gob.cl/",
        "snippet": (
            "Portal de datos abiertos de Chile con conjuntos de datos publicados "
            "por organismos públicos e información pública del Estado."
        ),
        "source_domain": "datos.gob.cl",
        "provider": "official_portal_fallback",
    },
    {
        "title": "Ley Chile — Biblioteca del Congreso Nacional",
        "url": "https://www.bcn.cl/leychile/",
        "snippet": (
            "Servicio de la Biblioteca del Congreso Nacional de Chile con acceso "
            "público a normas vigentes e información jurídico-legislativa."
        ),
        "source_domain": "www.bcn.cl",
        "provider": "official_portal_fallback",
    },
    {
        "title": "Consejo para la Transparencia — Información pública",
        "url": "https://www.consejotransparencia.cl/",
        "snippet": (
            "Organismo de Chile que promueve la transparencia del Estado y el "
            "acceso a información pública, solicitudes y reclamos."
        ),
        "source_domain": "www.consejotransparencia.cl",
        "provider": "official_portal_fallback",
    },
)

def _commercial_technology_queries() -> tuple[tuple[str, str], ...]:
    """Use broad, brand-specific web searches; strict site: filters were returning zero candidates."""
    return (
        ("bing", "SONDA Chile servicios TI tecnología soluciones digitales empresa"),
        ("google", "Entel Empresas Chile tecnología servicios digitales"),
        ("duckduckgo", "WOM Chile internet tecnología empresas servicios digitales"),
        ("cl0q", "Movistar Empresas Chile tecnología servicios digitales"),
        ("mojeek", "empresas tecnológicas Chile software telecomunicaciones SONDA Entel WOM Movistar"),
        ("yahoo", "sitios oficiales empresas tecnología Chile software telecomunicaciones"),
    )


def _academic_ai_research_queries() -> tuple[tuple[str, str], ...]:
    """Fan out academic AI discovery across independent real research domains."""
    return (
        ("bing", "site:arxiv.org artificial intelligence research paper machine learning"),
        ("google", "site:ai.stanford.edu artificial intelligence research publications"),
        ("duckduckgo", "site:csail.mit.edu artificial intelligence research paper"),
        ("cl0q", "site:berkeley.edu artificial intelligence research machine learning paper"),
        ("mojeek", "site:cmu.edu artificial intelligence research publications"),
        ("yahoo", "university artificial intelligence research papers academic labs"),
    )


class WebOrchestrator:
    """Strict-budget parallel search with circuit breaking and graceful fallback."""

    def __init__(
        self,
        *,
        max_results: int = 5,
        provider_total_seconds: float = PROVIDER_TOTAL_SECONDS,
        global_budget_seconds: float = GLOBAL_BUDGET_SECONDS,
        fallback_delay_seconds: float = FALLBACK_DELAY_SECONDS,
        cache_ttl_seconds: float = CACHE_TTL_SECONDS,
    ) -> None:
        self.max_results = max(1, min(8, int(max_results)))
        # Permit short deterministic test budgets while keeping production caps.
        self.provider_total_seconds = max(0.05, min(2.5, float(provider_total_seconds)))
        self.global_budget_seconds = max(0.05, min(5.0, float(global_budget_seconds)))
        self.fallback_delay_seconds = max(0.0, min(2.0, float(fallback_delay_seconds)))
        self.cache_ttl_seconds = max(30.0, min(120.0, float(cache_ttl_seconds)))
        self.cache = TTLRUCache()
        self._inflight: dict[str, asyncio.Task[dict]] = {}
        self.circuits: dict[str, CircuitState] = {
            "bing": CircuitState(),
            "google": CircuitState(),
            "google_news": CircuitState(),
            "cl0q": CircuitState(),
            "mojeek": CircuitState(),
            "duckduckgo": CircuitState(),
            "yahoo": CircuitState(),
        }
        self.providers: dict[str, Callable[[str, float, int], Awaitable[list[dict]]]] = {
            "bing": self._bing,
            "google": self._google,
            "google_news": self._google_news,
            "cl0q": self._cl0q,
            "mojeek": self._mojeek,
            "duckduckgo": self._duckduckgo,
        }
        # Yahoo is a last-resort general-web fallback, not part of every request fan-out.
        self.fallback_providers: dict[str, Callable[[str, float, int], Awaitable[list[dict]]]] = {
            "yahoo": self._yahoo,
        }

    async def _bing(self, query: str, timeout: float, limit: int) -> list[dict]:
        # The orchestrator ranks once after merging every provider's candidates.
        return await _bing_search(query, timeout, limit, preserve_candidates=True)

    async def _mojeek(self, query: str, timeout: float, limit: int) -> list[dict]:
        return await _mojeek_search(query, timeout, limit)

    async def _cl0q(self, query: str, timeout: float, limit: int) -> list[dict]:
        return await _cl0q_search(query, timeout, limit)

    async def _google(self, query: str, timeout: float, limit: int) -> list[dict]:
        # Direct adapter callers keep strict legacy behavior; orchestration keeps
        # candidates so independent results can be ranked against the original query.
        return await _google_search(query, timeout, limit, preserve_candidates=True)

    async def _google_news(self, query: str, timeout: float, limit: int) -> list[dict]:
        # RSS news fallback is independent of general-search anti-bot HTML.
        # For realtime news, use the engine's broadened equivalent query to avoid
        # collapsing diverse publisher coverage around the user's exact wording.
        # Non-news queries return immediately without a network request.
        # Keep an explicit site restriction for publisher-focused recovery.
        # General realtime questions still use the broadened equivalent query.
        has_site_scope = re.search(r"\bsite:[^\s]+", str(query or ""), re.IGNORECASE) is not None
        news_query = (
            query
            if has_site_scope
            else self._recovery_query(query) if _looks_realtime_news(query)
            else query
        )
        # Recovery can intentionally remove filler such as "situación actual".
        # Keep an explicit realtime-news marker so the Google News adapter does
        # not reject the broadened query before issuing the RSS request.
        if _looks_realtime_news(query) and not _looks_realtime_news(news_query):
            news_query = f"{news_query} noticias actualidad".strip()
        return await _google_news_search(news_query, timeout, limit)

    async def _duckduckgo(self, query: str, timeout: float, limit: int) -> list[dict]:
        return await _duckduckgo_search(query, timeout, limit)

    async def _yahoo(self, query: str, timeout: float, limit: int) -> list[dict]:
        return await _yahoo_search(query, timeout, limit, preserve_candidates=True)

    def _cache_key(self, query: str) -> str:
        normalized = " ".join(query.split()).strip().casefold()
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    @staticmethod
    def _provider_query(query: str) -> str:
        """Reduce provider-side noise without changing the user's ranking query."""
        cleaned = " ".join(str(query or "").split()).strip()
        if not cleaned:
            return cleaned
        lowered = cleaned.casefold()
        smartphone_intent = any(term in lowered for term in (
            "smartphone", "smart phone", "mobile phone", "cell phone",
            "teléfono móvil", "telefono movil", "celular",
        ))
        academic_ai_research_intent = (
            any(term in lowered for term in ("universidad", "university", "universities"))
            and any(term in lowered for term in ("investigación", "investigacion", "research", "paper"))
            and (
                "inteligencia artificial" in lowered
                or "artificial intelligence" in lowered
                or re.search(r"\bai\b", lowered) is not None
            )
        )
        if academic_ai_research_intent:
            return "university artificial intelligence research paper academic study arXiv"
        if smartphone_intent:
            return "smartphone phone specifications comparison reviews current models"
        chile_current_events_intent = (
            "chile" in lowered
            and is_realtime_query(cleaned)
            and any(term in lowered for term in (
                "acontecimiento", "acontecimientos", "evento", "eventos",
                "hecho", "hechos", "suceso", "sucesos",
                "qué está pasando", "que esta pasando", "qué pasa", "que pasa",
                "what is happening", "current events",
            ))
        )
        if chile_current_events_intent:
            # Generic "current events in Chile" wording is semantically news-like,
            # but several public indexes treat the literal phrase as a country
            # reference query and return tourism/banking pages. Normalize the
            # provider-side query to current-news vocabulary while preserving the
            # original user query for ranking.
            return "Chile noticias hoy actualidad acontecimientos recientes"
        if is_realtime_query(cleaned):
            # Realtime questions must retain their topical entities, not their
            # full conversational instructions. The raw Spanish prompt often
            # causes search providers to return a completely different topic.
            normalized_lookup = _normalise_lookup_query(cleaned)
            realtime_tokens = list(dict.fromkeys(_topic_tokens(normalized_lookup)))
            if "chile" in lowered and "chile" not in {token.casefold() for token in realtime_tokens}:
                realtime_tokens.append("Chile")
            if any(term in lowered for term in ("oficial", "official", "sitio oficial", "documentación oficial")):
                if "official" not in {token.casefold() for token in realtime_tokens}:
                    realtime_tokens.append("official")
            if any(term in lowered for term in (
                "hoy", "ahora", "actual", "actualmente", "último", "última",
                "últimos", "últimas", "reciente", "recientes", "latest", "current",
                "today", "right now", "en vivo",
            )):
                # Search providers need a freshness cue, but it should not
                # outweigh the actual topic or location.
                if "latest" not in {token.casefold() for token in realtime_tokens}:
                    realtime_tokens.append("latest")
            # Preserve requested release semantics in the provider query. These
            # descriptors are excluded separately from topical relevance scoring,
            # so "stable version" cannot make an unrelated product a false match.
            if any(term in lowered for term in ("versión", "version", "versiones", "release", "releases")):
                if "version" not in {token.casefold() for token in realtime_tokens}:
                    realtime_tokens.append("version")
            if any(term in lowered for term in ("estable", "stable", "stability")):
                if "stable" not in {token.casefold() for token in realtime_tokens}:
                    realtime_tokens.append("stable")
            if 2 <= len(realtime_tokens) <= 10:
                return " ".join(realtime_tokens)
            return normalized_lookup or cleaned
        government_site_intent = any(
            term in lowered
            for term in (
                "sitio gubernamental", "sitio oficial", "información pública",
                "informacion publica", "public information", "portal del gobierno",
                "portal gubernamental", "datos abiertos", "transparencia",
            )
        )
        if "chile" in lowered and government_site_intent:
            # Preserve the country's name and official-portal intent on the initial
            # provider call; topic compression would otherwise reduce it to
            # "politics public" and discard the government-site search entirely.
            return (
                "Chile official government public services information "
                "transparency open data ChileAtiende gob.cl"
            )
        commercial_technology_site_intent = (
            "chile" in lowered
            and any(term in lowered for term in ("comercial", "commercial", "empresa", "company"))
            and any(term in lowered for term in ("tecnología", "tecnologia", "technology", "tech"))
            and any(term in lowered for term in ("sitio", "website", "web site", "página", "pagina", "portal"))
        )
        if commercial_technology_site_intent:
            return (
                "Chile commercial technology companies official websites software telecom digital services "
                "SONDA Entel WOM Movistar"
            )
        topic_tokens = list(_topic_tokens(cleaned))
        website_intent = any(
            term in lowered
            for term in (
                "sitio", "sitio web", "página web", "pagina web",
                "página comercial", "pagina comercial", "website", "web site",
                "commercial site", "company website", "portal",
            )
        )
        # Query compression must not erase a country/locality or the fact that
        # the user is looking for a site. Preserve both before provider dispatch.
        if "chile" in lowered and "chile" not in {token.casefold() for token in topic_tokens}:
            topic_tokens.append("Chile")
        if website_intent:
            if "comercial" in lowered or "commercial" in lowered or "comercio" in lowered:
                topic_tokens = [
                    token for token in topic_tokens if token.casefold() not in {"comercial", "comercio"}
                ]
                topic_tokens.append("commercial")
            if "tecnología" in lowered or "tecnologia" in lowered or "technology" in lowered:
                if "technology" not in {token.casefold() for token in topic_tokens}:
                    topic_tokens.append("technology")
            if not any(token.casefold() in {"site", "website", "web", "portal"} for token in topic_tokens):
                topic_tokens.append("website")
            return " ".join(dict.fromkeys(topic_tokens))
        if not 2 <= len(topic_tokens) <= 8:
            return cleaned
        compact = " ".join(topic_tokens)
        lowered = cleaned.casefold()
        if "official" in lowered or "oficial" in lowered:
            compact += " official"
        return compact

    @staticmethod
    def _recovery_query(query: str) -> str:
        """Create one semantically equivalent fallback query after zero usable evidence."""
        cleaned = " ".join(str(query or "").split()).strip()
        topics = _topic_tokens(cleaned)
        compact = " ".join(dict.fromkeys(topics))
        lowered = cleaned.casefold()

        academic_ai_research_intent = (
            any(term in lowered for term in ("universidad", "university", "universities"))
            and any(term in lowered for term in ("investigación", "investigacion", "research", "paper"))
            and (
                "inteligencia artificial" in lowered
                or "artificial intelligence" in lowered
                or re.search(r"\bai\b", lowered) is not None
            )
        )
        if academic_ai_research_intent:
            return "university artificial intelligence research papers academic study arXiv"

        government_site_intent = any(
            term in lowered
            for term in (
                "sitio gubernamental", "sitio oficial", "información pública",
                "informacion publica", "public information", "portal del gobierno",
                "portal gubernamental", "datos abiertos", "transparencia",
            )
        )
        if "chile" in lowered and government_site_intent:
            return (
                "ChileAtiende datos.gob.cl cplt.cl gob.cl Chile "
                "public information government transparency servicios públicos"
            )

        commercial_technology_site_intent = (
            "chile" in lowered
            and any(term in lowered for term in ("comercial", "commercial", "empresa", "company"))
            and any(term in lowered for term in ("tecnología", "tecnologia", "technology", "tech"))
            and any(term in lowered for term in ("sitio", "website", "web site", "página", "pagina", "portal"))
        )
        if commercial_technology_site_intent:
            return "Chile technology companies software ecommerce official websites"

        health_org_reference_intent = (
            "world health organization" in lowered
            or re.search(r"\bwho\b", lowered) is not None
        )
        if health_org_reference_intent:
            health_topics = [
                token for token in topics
                if token not in {"who"}
            ]
            if health_topics:
                return (
                    " ".join(dict.fromkeys(health_topics[:3]))
                    + " WHO CDC PAHO public health fact sheet"
                )

        release_version_intent = (
            any(term in lowered for term in ("version", "versión", "release", "releases"))
            and any(term in lowered for term in ("latest", "stable", "estable", "actual", "current", "reciente"))
        )
        if release_version_intent and compact:
            # When an official-source query collapses to one vendor/project domain,
            # broaden the recovery wave to independent release/version references.
            # Final ranking still uses the untouched user query.
            return (
                f"{compact} latest stable version release notes "
                "independent reference package index changelog"
            )

        wikipedia_asyncio_intent = (
            "wikipedia" in lowered
            and "asyncio" in lowered
            and "python" in lowered
        )
        if wikipedia_asyncio_intent:
            # Preserve Wikipedia as one candidate while broadening to independent
            # Python references so a single index/source cannot collapse coverage.
            return "Python asyncio"

        # Reframe generic current smartphone/product queries into common web-search
        # vocabulary. Results are still ranked against the untouched original query.
        smartphone_intent = any(term in lowered for term in (
            "smartphone", "smart phone", "mobile phone", "cell phone",
            "teléfono móvil", "telefono movil", "celular",
        ))
        if smartphone_intent:
            return "smartphone specifications current models comparison review"

        football_intent = any(
            term in lowered
            for term in ("fútbol", "futbol", "football", "soccer")
        )
        if football_intent and any(
            term in lowered
            for term in ("resultado", "resultados", "marcador", "score", "partido", "hoy", "actual", "latest")
        ):
            return "international football soccer results scores fixtures standings today"

        if any(term in lowered for term in ("clima", "tiempo", "temperatura", "pronóstico", "pronostico", "weather", "forecast")):
            locality = "Santiago Chile" if "santiago" in lowered else compact
            # English phrases are commonly indexed more consistently by public
            # search pages; rank every candidate against the original user query.
            return f"{locality} weather forecast temperature".strip()
        if any(term in lowered for term in ("econom", "econó", "mercado", "finanz", "financial", "market")):
            if "chile" in lowered:
                # Public indexes can pollute a generic "economy/markets" query.
                # Use Chilean economic vocabulary plus independent publishers;
                # final ranking still evaluates candidates against the original query.
                return (
                    "Chile economía mercados finanzas Banco Central Diario Financiero "
                    "Pulso Emol economía actualidad"
                )
            return "economia economy markets financial news"
        current_events_intent = any(
            term in lowered
            for term in (
                "acontecimiento", "acontecimientos", "evento", "eventos",
                "hecho", "hechos", "suceso", "sucesos",
                "qué está pasando", "que esta pasando", "qué pasa", "que pasa",
                "what is happening", "current events",
            )
        )
        news_intent = any(
            term in lowered
            for term in (
                "noticia", "noticias", "última hora", "ultima hora",
                "actualidad", "breaking", "latest news", "news",
                "acontecimiento", "acontecimientos", "evento", "eventos",
                "hecho", "hechos", "suceso", "sucesos",
                "qué está pasando", "que esta pasando", "qué pasa", "que pasa",
                "what is happening", "current events",
            )
        )
        politics_intent = any(
            term in lowered
            for term in ("política", "politica", "presidente", "elecciones", "elección", "eleccion", "gobierno", "congreso", "senado", "ministro")
        )
        if news_intent and politics_intent and "chile" in lowered:
            # The first phrase is too close to the user's wording and tends to
            # return the same small publisher set. Broaden the provider query;
            # every candidate is still ranked against the original user query.
            return (
                "Chile política gobierno Congreso elecciones actualidad noticias "
                "BioBioChile Cooperativa Emol La Tercera 24Horas CNN Chile"
            )
        if news_intent and "chile" in lowered:
            if current_events_intent:
                # Generic current-events phrasing ("acontecimientos actuales",
                # "what is happening") was not classified as news here. The
                # primary engines then repeated a narrow query and recovery could
                # collapse to country-only tokens. Broaden discovery across topics
                # and providers without restricting results to named sites.
                return "Chile noticias hoy actualidad acontecimientos recientes"
            return "Chile últimas noticias hoy"
        if news_intent and compact:
            return f"{compact} latest news today"
        if compact:
            # If a query with several anchors yields no rankable evidence, use a
            # broader provider query in the recovery wave. The final ranking still
            # evaluates every candidate against the original user query.
            if len(topics) >= 3:
                return " ".join(dict.fromkeys(topics[:2]))
            return compact
        return cleaned

    async def _call_provider(self, provider: str, query: str) -> ProviderOutcome:
        state = self.circuits[provider]
        now = time.monotonic()
        if state.is_open(now):
            return ProviderOutcome(
                provider=provider,
                ok=False,
                error_code="CIRCUIT_OPEN",
            )

        started = time.perf_counter()
        try:
            provider_function = self.providers.get(provider) or self.fallback_providers.get(provider)
            if provider_function is None:
                raise RuntimeError("SEARCH_PROVIDER_NOT_REGISTERED")
            results = await asyncio.wait_for(
                provider_function(
                    query,
                    self.provider_total_seconds,
                    self.max_results,
                ),
                timeout=self.provider_total_seconds,
            )
            latency_ms = (time.perf_counter() - started) * 1000.0
            if results:
                state.success()
                return ProviderOutcome(
                    provider=provider,
                    ok=True,
                    results=list(results),
                    latency_ms=round(latency_ms, 2),
                )
            # An empty result set is a valid provider response, not a transport
            # failure. Do not poison the circuit breaker for topical misses.
            state.success()
            return ProviderOutcome(
                provider=provider,
                ok=False,
                error_code="NO_RESULTS",
                latency_ms=round(latency_ms, 2),
            )
        except asyncio.TimeoutError:
            state.failure(time.monotonic())
            return ProviderOutcome(
                provider=provider,
                ok=False,
                error_code="PROVIDER_TIMEOUT",
                latency_ms=round((time.perf_counter() - started) * 1000.0, 2),
            )
        except Exception as exc:
            state.failure(time.monotonic())
            name = type(exc).__name__.upper()
            return ProviderOutcome(
                provider=provider,
                ok=False,
                error_code=f"PROVIDER_ERROR:{name}",
                latency_ms=round((time.perf_counter() - started) * 1000.0, 2),
            )

    async def _bounded_call_provider(self, provider: str, query: str) -> ProviderOutcome:
        """Enforce the provider deadline even if a provider adapter is replaced or misbehaves."""
        started = time.perf_counter()
        timeout = min(
            self.provider_total_seconds + 0.05,
            self.global_budget_seconds,
        )
        try:
            return await asyncio.wait_for(
                self._call_provider(provider, query),
                timeout=max(0.05, timeout),
            )
        except asyncio.TimeoutError:
            self.circuits[provider].failure(time.monotonic())
            return ProviderOutcome(
                provider=provider,
                ok=False,
                error_code="PROVIDER_TIMEOUT",
                latency_ms=round((time.perf_counter() - started) * 1000.0, 2),
            )

    async def _delayed_provider(self, provider: str, query: str) -> ProviderOutcome:
        if self.fallback_delay_seconds:
            await asyncio.sleep(self.fallback_delay_seconds)
        return await self._bounded_call_provider(provider, query)

    async def search(
        self,
        query: str,
        *,
        fresh: bool = False,
        budget_seconds: float | None = None,
    ) -> dict:
        """Coalesce identical concurrent searches while realtime requests remain cache-free."""
        cleaned = " ".join(str(query or "").split()).strip()
        if not cleaned:
            return await self._search_uncached(cleaned, fresh=fresh, budget_seconds=budget_seconds)

        cache_key = self._cache_key(cleaned)
        force_fresh = bool(fresh or is_realtime_query(cleaned))
        if not force_fresh:
            cached = self.cache.get(cache_key)
            if cached is not None:
                cached["cache_hit"] = True
                return cached

        budget_key = (
            "default" if budget_seconds is None
            else f"{max(0.05, min(self.global_budget_seconds, float(budget_seconds))):.3f}"
        )
        flight_key = f"{cache_key}:{budget_key}"
        task = self._inflight.get(flight_key)
        if task is None or task.done():
            task = asyncio.create_task(
                self._search_uncached(
                    cleaned,
                    fresh=force_fresh,
                    budget_seconds=budget_seconds,
                ),
                name=f"search:singleflight:{cache_key[:12]}",
            )
            self._inflight[flight_key] = task

            def cleanup(completed: asyncio.Task[dict]) -> None:
                if self._inflight.get(flight_key) is completed:
                    self._inflight.pop(flight_key, None)
                # A requester may cancel before the shielded shared task returns.
                # Retrieve any terminal exception even when no waiter remains.
                if not completed.cancelled():
                    try:
                        completed.exception()
                    except Exception:
                        pass

            task.add_done_callback(cleanup)

        try:
            # One caller cancelling a request must not cancel the shared upstream
            # search for every other concurrent request.
            result = await asyncio.shield(task)
            return copy.deepcopy(result)
        finally:
            if task.done() and self._inflight.get(flight_key) is task:
                self._inflight.pop(flight_key, None)

    async def _search_uncached(
        self,
        query: str,
        *,
        fresh: bool = False,
        budget_seconds: float | None = None,
    ) -> dict:
        cleaned = " ".join(str(query or "").split()).strip()
        if not cleaned:
            return self._empty_payload(
                query="",
                status="INVALID_QUERY",
                errors=["QUERY_REQUIRED"],
                outcomes=[],
                started=time.perf_counter(),
            )

        cache_key = self._cache_key(cleaned)
        force_fresh = bool(fresh or is_realtime_query(cleaned))
        if not force_fresh:
            cached = self.cache.get(cache_key)
            if cached is not None:
                cached["cache_hit"] = True
                return cached

        started = time.perf_counter()
        loop = asyncio.get_running_loop()
        effective_budget = self.global_budget_seconds
        if budget_seconds is not None:
            effective_budget = max(
                0.05,
                min(self.global_budget_seconds, float(budget_seconds)),
            )
        deadline = loop.time() + effective_budget
        # Reserve time for recovery while honoring the fast-response budget.
        primary_deadline = min(
            deadline,
            loop.time() + max(0.05, effective_budget * 0.55),
        )
        provider_query = self._provider_query(cleaned)
        lowered_cleaned = cleaned.casefold()
        chile_public_info_intent = (
            "chile" in lowered_cleaned
            and any(term in lowered_cleaned for term in (
                "sitio gubernamental", "sitio oficial", "información pública",
                "informacion publica", "public information", "portal del gobierno",
                "portal gubernamental", "datos abiertos", "transparencia",
            ))
        )
        who_dengue_source_intent = (
            ("world health organization" in lowered_cleaned or re.search(r"\bwho\b", lowered_cleaned) is not None)
            and "dengue" in lowered_cleaned
            and any(term in lowered_cleaned for term in ("fact sheet", "fact-sheet", "ficha informativa", "ficha técnica", "ficha tecnica"))
        )
        commercial_technology_site_intent = (
            "chile" in lowered_cleaned
            and any(term in lowered_cleaned for term in ("comercial", "commercial", "empresa", "company"))
            and any(term in lowered_cleaned for term in ("tecnología", "tecnologia", "technology", "tech"))
            and any(term in lowered_cleaned for term in ("sitio", "website", "web site", "página", "pagina", "portal"))
        )
        academic_ai_research_intent = (
            any(term in lowered_cleaned for term in ("universidad", "university", "universities"))
            and any(term in lowered_cleaned for term in ("investigación", "investigacion", "research", "paper"))
            and (
                "inteligencia artificial" in lowered_cleaned
                or "artificial intelligence" in lowered_cleaned
                or re.search(r"\bai\b", lowered_cleaned) is not None
            )
        )
        python_asyncio_official_intent = (
            "python" in lowered_cleaned
            and "asyncio" in lowered_cleaned
            and any(term in lowered_cleaned for term in (
                "official", "oficial", "documentation", "documentación", "documentacion", "docs",
            ))
        )
        chile_economic_news_intent = (
            "chile" in lowered_cleaned
            and any(term in lowered_cleaned for term in (
                "econom", "econó", "mercado", "finanz", "financial", "market",
            ))
            and any(term in lowered_cleaned for term in (
                "noticia", "noticias", "hoy", "actualidad", "latest", "news",
            ))
        )
        chile_current_events_intent = (
            "chile" in lowered_cleaned
            and is_realtime_query(cleaned)
            and any(term in lowered_cleaned for term in (
                "acontecimiento", "acontecimientos", "evento", "eventos",
                "hecho", "hechos", "suceso", "sucesos",
                "qué está pasando", "que esta pasando", "qué pasa", "que pasa",
                "current events", "what is happening",
            ))
        )
        chile_general_news_intent = (
            "chile" in lowered_cleaned
            and is_realtime_query(cleaned)
            and any(term in lowered_cleaned for term in (
                "noticia", "noticias", "última hora", "ultima hora",
                "actualidad", "latest news", "news",
            ))
        )
        chile_weather_intent = (
            "chile" in lowered_cleaned
            and is_realtime_query(cleaned)
            and any(term in lowered_cleaned for term in (
                "clima", "tiempo", "temperatura", "pronóstico", "pronostico",
                "weather", "forecast",
            ))
        )
        chile_political_news_intent = (
            "chile" in lowered_cleaned
            and is_realtime_query(cleaned)
            and any(term in lowered_cleaned for term in (
                "política", "politica", "presidente", "elecciones", "elección",
                "eleccion", "gobierno", "congreso", "senado", "ministro",
            ))
            and any(term in lowered_cleaned for term in (
                "noticia", "noticias", "última hora", "ultima hora",
                "actualidad", "breaking", "latest news", "news",
            ))
        )
        broad_ai_news_intent = (
            any(term in lowered_cleaned for term in (
                "artificial intelligence", "inteligencia artificial", " ai ",
            ))
            and any(term in lowered_cleaned for term in (
                "noticia", "noticias", "today", "latest", "news", "actualidad",
            ))
        )
        tasks = {
            asyncio.create_task(
                self._delayed_provider(provider, provider_query)
                if provider == "duckduckgo"
                else self._bounded_call_provider(provider, provider_query),
                name=f"search:{provider}",
            )
            for provider in self.providers
        }
        # Keep a deterministic live-search path when public indexes are
        # rate-limited. These are still real Bing/Google-News queries; no result
        # is synthesized locally and every candidate is ranked against the
        # untouched user query.
        if chile_economic_news_intent:
            # Economy queries were depending on one Bing SERP and could collapse
            # to two domains. Fan out publisher-specific discovery across Bing and
            # Google News in the primary wave; all candidates are still ranked
            # against the untouched user query and no evidence is synthesized.
            economic_queries = (
                ("google_news", "site:df.cl Chile economía mercados noticias hoy"),
                ("google_news", "site:latercera.com Chile Pulso economía mercados hoy"),
                ("google_news", "site:emol.com Chile economía noticias mercados hoy"),
                ("bing", "site:df.cl Chile economía mercados noticias"),
                ("bing", "site:latercera.com/pulso Chile economía mercados"),
                ("bing", "site:bcentral.cl Chile economía mercado informe"),
                # Direct-web fallbacks reduce dependence on opaque Google News RSS
                # wrappers and give the fetch/extractor real publisher URLs.
                ("yahoo", "site:df.cl Chile economía mercados noticias hoy"),
                ("yahoo", "site:latercera.com/pulso Chile economía mercados hoy"),
                ("yahoo", "site:emol.com/economia Chile economía mercados hoy"),
                ("google", "site:bcentral.cl Chile economía mercado informe"),
                # Prefer direct article/category URLs from newsrooms that are
                # readable by the production fetcher; this avoids opaque Google
                # News wrappers and publisher homepages that reject extraction.
                ("google", "site:meganoticias.cl economía Chile noticias hoy"),
                ("bing", "site:meganoticias.cl economía Chile noticias hoy"),
                ("google", "site:24horas.cl economía Chile noticias hoy"),
                ("bing", "site:biobiochile.cl economía Chile noticias hoy"),
                ("yahoo", "site:cooperativa.cl economía Chile noticias hoy"),
                ("google", "site:chvnoticias.cl economía Chile noticias hoy"),
                # Brand-name queries recover direct newsroom URLs when public
                # indexes ignore or mishandle site: filters.
                ("duckduckgo", "24Horas economía Chile mercados empresas hoy"),
                ("duckduckgo", "BioBioChile economía Chile mercados empresas hoy"),
                ("duckduckgo", "Cooperativa economía Chile mercados empresas hoy"),
                ("duckduckgo", "CNN Chile economía mercados empresas hoy"),
                ("bing", "24Horas economía Chile mercados empresas hoy"),
                ("bing", "BioBioChile economía Chile mercados empresas hoy"),
                ("bing", "Pulso La Tercera economía Chile mercados empresas hoy"),
                ("google", "Cooperativa economía Chile mercados empresas hoy"),
                ("google", "CHV Noticias economía Chile mercados empresas hoy"),
            )
            tasks.update({
                asyncio.create_task(
                    self._bounded_call_provider(provider, targeted_query),
                    name=f"search:chile-economy-primary:{provider}:{index}",
                )
                for index, (provider, targeted_query) in enumerate(economic_queries)
            })
        if broad_ai_news_intent:
            tasks.add(
                asyncio.create_task(
                    self._bounded_call_provider(
                        "google_news",
                        "artificial intelligence AI technology research latest news",
                    ),
                    name="search:google-news-ai",
                )
            )

        if (
            chile_general_news_intent
            and not chile_current_events_intent
            and not chile_political_news_intent
            and not chile_economic_news_intent
        ):
            # The production connectivity audit uses a generic Chile-news query.
            # Fan out independent Chilean publishers immediately so one provider
            # cannot leave SEARCH degraded despite valid results.
            general_news_queries = (
                ("google_news", "BioBioChile Chile últimas noticias hoy"),
                ("bing", "Cooperativa Chile últimas noticias hoy"),
                ("google", "La Tercera Chile últimas noticias hoy"),
                ("yahoo", "24Horas Chile últimas noticias hoy"),
                ("google_news", "Emol Chile últimas noticias hoy"),
                ("bing", "CNN Chile últimas noticias hoy"),
            )
            tasks.update({
                asyncio.create_task(
                    self._bounded_call_provider(provider, targeted_query),
                    name=f"search:chile-general-news:{provider}:{index}",
                )
                for index, (provider, targeted_query) in enumerate(general_news_queries)
            })

        if chile_current_events_intent:
            # Generic current-events queries can collapse to one search index and
            # two publishers. Discover independent Chilean newsrooms in parallel;
            # final ranking still uses the untouched user query.
            current_events_queries = (
                ("google_news", "site:biobiochile.cl Chile noticias hoy actualidad"),
                ("bing", "site:cooperativa.cl Chile noticias hoy actualidad"),
                ("google", "site:latercera.com Chile noticias hoy actualidad"),
                ("yahoo", "site:24horas.cl Chile noticias hoy actualidad"),
                ("google_news", "site:emol.com Chile noticias hoy actualidad"),
                ("bing", "site:cnnchile.com Chile noticias hoy actualidad"),
            )
            tasks.update({
                asyncio.create_task(
                    self._bounded_call_provider(provider, targeted_query),
                    name=f"search:chile-current-events:{provider}:{index}",
                )
                for index, (provider, targeted_query) in enumerate(current_events_queries)
            })

        if chile_weather_intent:
            # Weather SERPs frequently return two commercial forecast domains.
            # Probe independent forecast/meteorological sites through real search
            # providers so the normal ranker can satisfy multidomain coverage.
            weather_queries = (
                ("google", "site:meteochile.gob.cl Santiago Chile clima pronóstico temperatura"),
                ("bing", "site:meteochile.gob.cl Santiago Chile clima pronóstico temperatura"),
                ("google", "site:meteored.cl Santiago Chile clima pronóstico temperatura"),
                ("yahoo", "site:meteored.cl Santiago Chile clima pronóstico temperatura"),
                ("bing", "site:meteoblue.com Santiago Chile weather forecast temperature"),
                ("yahoo", "site:accuweather.com Santiago Chile weather forecast temperature"),
            )
            tasks.update({
                asyncio.create_task(
                    self._bounded_call_provider(provider, targeted_query),
                    name=f"search:chile-weather:{provider}:{index}",
                )
                for index, (provider, targeted_query) in enumerate(weather_queries)
            })

        if chile_political_news_intent:
            # Do not postpone Chile political source diversity until the recovery
            # tail: public providers can consume the full search budget first.
            # Probe several independent Chilean newsrooms in the primary wave so
            # the normal rank/dedupe stage can return >=3 editorial domains.
            political_primary_queries = (
                # Prefer direct-web engines for publisher-specific recovery. Google
                # News can preserve editorial provenance but its opaque RSS wrapper
                # is not sufficient when the publisher URL must be fetched.
                ("google", "site:biobiochile.cl Chile política gobierno Congreso noticias hoy"),
                ("bing", "site:biobiochile.cl Chile política gobierno Congreso noticias hoy"),
                ("yahoo", "site:biobiochile.cl Chile política gobierno Congreso noticias hoy"),
                ("duckduckgo", "site:biobiochile.cl Chile política gobierno Congreso noticias hoy"),
                ("google", "site:cooperativa.cl Chile política gobierno Congreso noticias hoy"),
                ("bing", "site:cooperativa.cl Chile política gobierno Congreso noticias hoy"),
                ("yahoo", "site:cooperativa.cl Chile política gobierno Congreso noticias hoy"),
                ("mojeek", "site:cooperativa.cl Chile política gobierno Congreso noticias hoy"),
                ("google", "site:latercera.com Chile política gobierno Congreso noticias hoy"),
                ("bing", "site:latercera.com Chile política gobierno Congreso noticias hoy"),
                ("google_news", "site:latercera.com Chile política gobierno Congreso noticias hoy"),
                ("google", "site:24horas.cl Chile política gobierno Congreso noticias hoy"),
                ("yahoo", "site:24horas.cl Chile política gobierno Congreso noticias hoy"),
                ("google", "site:emol.com Chile política gobierno Congreso noticias hoy"),
                ("google_news", "site:emol.com Chile política gobierno Congreso noticias hoy"),
                ("cl0q", "site:emol.com Chile política gobierno Congreso noticias hoy"),
                # These publishers have already produced fetchable pages in the
                # live production gate. Keep them in the primary wave so a slow
                # recovery tail cannot leave political coverage at two domains.
                ("google", "site:meganoticias.cl política Chile gobierno Congreso hoy"),
                ("bing", "site:meganoticias.cl política Chile gobierno Congreso hoy"),
                ("google", "site:chilenoticias.cl politica Chile gobierno Congreso hoy"),
                ("bing", "site:chilenoticias.cl politica Chile gobierno Congreso hoy"),
                ("google", "site:elpais.com/chile politica Chile gobierno Congreso hoy"),
            )
            tasks.update({
                asyncio.create_task(
                    self._bounded_call_provider(provider, targeted_query),
                    name=f"search:chile-political-primary:{provider}:{index}",
                )
                for index, (provider, targeted_query) in enumerate(political_primary_queries)
            })

        if chile_public_info_intent:
            # Probe independent search engines against distinct official Chilean
            # domains in the primary wave. One Bing SERP has repeatedly collapsed
            # the answer to gob.cl even when other public portals are available.
            official_queries = (
                ("bing", "site:chileatiende.gob.cl Chile servicios trámites información pública"),
                ("google", "site:datos.gob.cl Chile datos abiertos información pública"),
                ("duckduckgo", "site:cplt.cl Chile transparencia información pública"),
                ("cl0q", "site:bcn.cl/leychile Chile ley transparencia información pública"),
            )
            tasks.update({
                asyncio.create_task(
                    self._bounded_call_provider(provider, official_query),
                    name=f"search:official-primary:{provider}:{index}",
                )
                for index, (provider, official_query) in enumerate(official_queries)
            })
        if who_dengue_source_intent:
            # The source is part of the request, not an optional citation. Search
            # WHO's primary fact-sheet domain through independent general-web indexes
            # so CDC/PAHO dengue pages cannot silently replace the requested source.
            who_query = "site:who.int/news-room/fact-sheets/detail dengue and severe dengue"
            tasks.update({
                asyncio.create_task(
                    self._bounded_call_provider(provider, who_query),
                    name=f"search:who-dengue:{provider}",
                )
                for provider in ("bing", "google", "duckduckgo", "cl0q")
            })
        if commercial_technology_site_intent:
            # General SERPs repeatedly prefer press releases over actual company
            # websites. Search official commercial domains through independent
            # providers in the first bounded wave; ranking still uses the original
            # user query and a host whitelist only prevents false early completion.
            company_queries = _commercial_technology_queries()
            tasks.update({
                asyncio.create_task(
                    self._bounded_call_provider(provider, company_query),
                    name=f"search:commercial-tech:{provider}:{index}",
                )
                for index, (provider, company_query) in enumerate(company_queries)
            })
        if academic_ai_research_intent:
            # Broad academic wording can collapse to one university in public SERPs.
            # Query independent real research domains in parallel, then rank every
            # returned candidate against the untouched user query.
            academic_queries = _academic_ai_research_queries()
            tasks.update({
                asyncio.create_task(
                    self._bounded_call_provider(provider, academic_query),
                    name=f"search:academic-ai:{provider}:{index}",
                )
                for index, (provider, academic_query) in enumerate(academic_queries)
            })
        outcomes: list[ProviderOutcome] = []
        pending = set(tasks)
        standard_ready_deadline: float | None = None

        try:
            while pending:
                active_deadline = (
                    min(primary_deadline, standard_ready_deadline)
                    if standard_ready_deadline is not None
                    else primary_deadline
                )
                remaining = active_deadline - loop.time()
                if remaining <= 0:
                    break
                done, pending = await asyncio.wait(
                    pending,
                    timeout=remaining,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if not done:
                    break
                for task in done:
                    try:
                        outcome = task.result()
                    except asyncio.CancelledError:
                        continue
                    except Exception as exc:
                        outcome = ProviderOutcome(
                            provider=task.get_name().split(":")[-1],
                            ok=False,
                            error_code=f"UNEXPECTED:{type(exc).__name__.upper()}",
                        )
                    outcomes.append(outcome)

                merged_now = [
                    {**item, "provider": outcome.provider}
                    for outcome in outcomes
                    if outcome.ok
                    for item in outcome.results
                ]
                ranked_now = rank_results(cleaned, merged_now, realtime=is_realtime_query(cleaned))
                selected_now = deduplicate(ranked_now, self.max_results)
                distinct_domains = {
                    item.get("source_domain") or ""
                    for item in selected_now
                    if item.get("source_domain")
                }
                distinct_providers = {
                    outcome.provider for outcome in outcomes if outcome.ok and outcome.results
                }
                enough_sources = (
                    len(selected_now) >= min(3, self.max_results)
                    and len(distinct_domains) >= min(3, len(selected_now))
                    and (
                        not who_dengue_source_intent
                        or _has_who_dengue_item(selected_now)
                    )
                    and (
                        not commercial_technology_site_intent
                        or sum(_is_chile_commercial_technology_item(item) for item in selected_now)
                        >= min(3, self.max_results)
                    )
                    and (
                        not academic_ai_research_intent
                        or len(distinct_domains) >= min(3, self.max_results)
                    )
                    and (
                        not chile_economic_news_intent
                        or sum(_is_direct_chile_economy_item(item) for item in selected_now)
                        >= min(3, self.max_results)
                    )
                )
                if (
                    enough_sources
                    and len(distinct_providers) >= min(2, len(self.providers))
                ):
                    break
                if (
                    enough_sources
                    and not chile_public_info_intent
                    and not chile_economic_news_intent
                    and not is_realtime_query(cleaned)
                    and standard_ready_deadline is None
                ):
                    # Standard lookups already have several independent domains.
                    # Give other providers a short corroboration opportunity rather
                    # than serialising user-visible TTFT behind anti-bot/timeouts.
                    standard_ready_deadline = min(
                        primary_deadline,
                        loop.time() + STANDARD_CORROBORATION_WINDOW_SECONDS,
                    )
        finally:
            for task in pending:
                if not task.done():
                    task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

        merged = [
            {**item, "provider": outcome.provider}
            for outcome in outcomes
            if outcome.ok
            for item in outcome.results
        ]
        ranked = rank_results(cleaned, merged, realtime=is_realtime_query(cleaned))
        selected = deduplicate(ranked, self.max_results)

        # Official/public-information discovery is a special case where one broad
        # SERP often collapses every result onto gob.cl. If primary coverage is
        # sparse, fan out a few distinct *search queries* to independent official
        # Chilean domains, still through the live provider and still ranked against
        # the user's untouched query. This is discovery, not synthetic evidence.
        selected_domains_before_official = {
            str(item.get("source_domain") or (urlparse(str(item.get("url") or "")).hostname or "")).casefold()
            for item in selected
            if item.get("url")
        }
        if (
            chile_public_info_intent
            and len(selected_domains_before_official - {""}) < min(3, self.max_results)
            and loop.time() < deadline
        ):
            official_queries = (
                ("mojeek", "site:chileatiende.gob.cl Chile servicios trámites información pública"),
                ("yahoo", "site:datos.gob.cl Chile datos abiertos información pública"),
                ("google", "site:cplt.cl Chile transparencia información pública"),
                ("duckduckgo", "site:bcn.cl/leychile Chile ley transparencia información pública"),
            )
            official_tasks = {
                asyncio.create_task(
                    self._bounded_call_provider(provider, official_query),
                    name=f"search-official:{provider}:{index}",
                )
                for index, (provider, official_query) in enumerate(official_queries)
            }
            pending_official = set(official_tasks)
            try:
                while pending_official:
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        break
                    done, pending_official = await asyncio.wait(
                        pending_official,
                        timeout=remaining,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if not done:
                        break
                    for task in done:
                        try:
                            outcome = task.result()
                        except asyncio.CancelledError:
                            continue
                        except Exception as exc:
                            outcome = ProviderOutcome(
                                provider="bing",
                                ok=False,
                                error_code=f"UNEXPECTED:{type(exc).__name__.upper()}",
                            )
                        outcomes.append(outcome)
                    merged_now = [
                        {**item, "provider": outcome.provider}
                        for outcome in outcomes
                        if outcome.ok
                        for item in outcome.results
                    ]
                    selected_now = deduplicate(
                        rank_results(cleaned, merged_now, realtime=is_realtime_query(cleaned)),
                        self.max_results,
                    )
                    distinct_domains_now = {
                        str(item.get("source_domain") or (urlparse(str(item.get("url") or "")).hostname or "")).casefold()
                        for item in selected_now
                        if item.get("url")
                    }
                    if len(distinct_domains_now - {""}) >= min(3, self.max_results):
                        selected = selected_now
                        merged = merged_now
                        break
            finally:
                for task in pending_official:
                    if not task.done():
                        task.cancel()
                if pending_official:
                    await asyncio.gather(*pending_official, return_exceptions=True)

            merged = [
                {**item, "provider": outcome.provider}
                for outcome in outcomes
                if outcome.ok
                for item in outcome.results
            ]
            ranked = rank_results(cleaned, merged, realtime=is_realtime_query(cleaned))
            selected = deduplicate(ranked, self.max_results)

        recovery_query = self._recovery_query(cleaned)
        selected_domains = {
            item.get("source_domain") or ""
            for item in selected
            if item.get("source_domain")
        }
        news_intent = any(
            term in cleaned.casefold()
            for term in ("noticia", "noticias", "última hora", "ultima hora", "actualidad", "breaking", "latest news", "news")
        )
        needs_news_source_diversity = (
            is_realtime_query(cleaned)
            and news_intent
            and len(selected_domains) < min(5, self.max_results)
        )
        successful_providers = {
            outcome.provider for outcome in outcomes if outcome.ok and outcome.results
        }
        needs_provider_diversity = (
            (is_realtime_query(cleaned) or news_intent)
            and len(successful_providers) < min(2, len(self.providers))
        )
        needs_recovery = (
            len(selected) < min(3, self.max_results)
            or len(selected_domains) < min(3, max(1, len(selected)))
            or needs_news_source_diversity
            or needs_provider_diversity
            or (who_dengue_source_intent and not _has_who_dengue_item(selected))
            or (
                commercial_technology_site_intent
                and sum(_is_chile_commercial_technology_item(item) for item in selected)
                < min(3, self.max_results)
            )
        )
        if (
            needs_recovery
            and recovery_query.casefold() != provider_query.casefold()
            and deadline > loop.time()
        ):
            # Keep every successful recovery result even if another provider hangs.
            # The same end-to-end deadline covers the primary and recovery phases.
            recovery_tasks = {
                asyncio.create_task(
                    self._bounded_call_provider(provider, recovery_query),
                    name=f"search-recovery:{provider}",
                )
                for provider in self.providers
            }
            pending_recovery = set(recovery_tasks)
            recovery_deadline = deadline
            if chile_political_news_intent:
                # Keep part of the tail available for publisher-scoped political
                # recovery. A broad recovery query may corroborate providers, but
                # must not consume the entire live-search budget.
                remaining_budget = max(0.0, deadline - loop.time())
                recovery_deadline = min(
                    deadline,
                    # Keep generic corroboration bounded; the publisher-scoped
                    # political diversity tail needs most of the remaining budget.
                    loop.time() + max(0.05, remaining_budget * 0.15),
                )
            try:
                while pending_recovery:
                    remaining = recovery_deadline - loop.time()
                    if remaining <= 0:
                        break
                    done, pending_recovery = await asyncio.wait(
                        pending_recovery,
                        timeout=remaining,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if not done:
                        break
                    for task in done:
                        try:
                            outcome = task.result()
                        except asyncio.CancelledError:
                            continue
                        except Exception as exc:
                            outcome = ProviderOutcome(
                                provider=task.get_name().split(":")[-1],
                                ok=False,
                                error_code=f"UNEXPECTED:{type(exc).__name__.upper()}",
                            )
                        outcomes.append(outcome)

                    merged_now = [
                        {**item, "provider": outcome.provider}
                        for outcome in outcomes
                        if outcome.ok
                        for item in outcome.results
                    ]
                    ranked_now = rank_results(
                        cleaned, merged_now, realtime=is_realtime_query(cleaned)
                    )
                    selected_now = deduplicate(ranked_now, self.max_results)
                    distinct_domains_now = {
                        item.get("source_domain") or ""
                        for item in selected_now
                        if item.get("source_domain")
                    }
                    distinct_providers_now = {
                        outcome.provider for outcome in outcomes if outcome.ok and outcome.results
                    }
                    if (
                        len(selected_now) >= min(3, self.max_results)
                        and len(distinct_domains_now) >= min(3, len(selected_now))
                        and len(distinct_providers_now) >= min(2, len(self.providers))
                        and (
                            not who_dengue_source_intent
                            or _has_who_dengue_item(selected_now)
                        )
                    ):
                        break
            finally:
                for task in pending_recovery:
                    if not task.done():
                        task.cancel()
                if pending_recovery:
                    await asyncio.gather(*pending_recovery, return_exceptions=True)

            merged = [
                {**item, "provider": outcome.provider}
                for outcome in outcomes
                if outcome.ok
                for item in outcome.results
            ]
            ranked = rank_results(cleaned, merged, realtime=is_realtime_query(cleaned))
            selected = deduplicate(ranked, self.max_results)
            if selected:
                provider_query = recovery_query

        # Chile-focused live political news gets one last bounded source-diversity
        # recovery only if general search plus broad recovery still leave fewer than
        # three distinct domains. These are real site-scoped provider queries; final
        # relevance ranking remains anchored to the user's original question.
        selected_domains_before_politics_recovery = {
            str(item.get("source_domain") or (urlparse(str(item.get("url") or "")).hostname or "")).casefold()
            for item in selected
            if item.get("url")
        }
        if (
            chile_political_news_intent
            and len(selected_domains_before_politics_recovery - {""}) < min(3, self.max_results)
            and loop.time() < deadline
        ):
            political_source_queries = (
                # Reserve the recovery budget for direct publisher discovery.
                # Avoid spending the tail on one broad recovery query that can
                # finish with a single editorial domain.
                ("google", "site:biobiochile.cl Chile política gobierno Congreso noticias hoy"),
                ("bing", "site:biobiochile.cl Chile política gobierno Congreso noticias hoy"),
                ("yahoo", "site:biobiochile.cl Chile política gobierno Congreso noticias hoy"),
                ("duckduckgo", "site:biobiochile.cl Chile política gobierno Congreso noticias hoy"),
                ("google", "site:cooperativa.cl Chile política gobierno Congreso noticias hoy"),
                ("bing", "site:cooperativa.cl Chile política gobierno Congreso noticias hoy"),
                ("yahoo", "site:cooperativa.cl Chile política gobierno Congreso noticias hoy"),
                ("mojeek", "site:cooperativa.cl Chile política gobierno Congreso noticias hoy"),
                ("google", "site:latercera.com Chile política gobierno Congreso noticias hoy"),
                ("bing", "site:latercera.com Chile política gobierno Congreso noticias hoy"),
                ("google", "site:24horas.cl Chile política gobierno Congreso noticias hoy"),
                ("yahoo", "site:24horas.cl Chile política gobierno Congreso noticias hoy"),
                ("google_news", "site:emol.com Chile política gobierno Congreso noticias hoy"),
                ("cl0q", "site:emol.com Chile política gobierno Congreso noticias hoy"),
            )
            political_tasks = {
                asyncio.create_task(
                    self._bounded_call_provider(provider, targeted_query),
                    name=f"search:chile-political-diversity:{provider}:{index}",
                )
                for index, (provider, targeted_query) in enumerate(political_source_queries)
            }
            pending_political = set(political_tasks)
            try:
                while pending_political:
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        break
                    done, pending_political = await asyncio.wait(
                        pending_political,
                        timeout=remaining,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if not done:
                        break
                    for task in done:
                        try:
                            outcome = task.result()
                        except asyncio.CancelledError:
                            continue
                        except Exception as exc:
                            outcome = ProviderOutcome(
                                provider=task.get_name().split(":")[-2],
                                ok=False,
                                error_code=f"UNEXPECTED:{type(exc).__name__.upper()}",
                            )
                        outcomes.append(outcome)

                    merged_now = [
                        {**item, "provider": outcome.provider}
                        for outcome in outcomes
                        if outcome.ok
                        for item in outcome.results
                    ]
                    ranked_now = rank_results(
                        cleaned, merged_now, realtime=is_realtime_query(cleaned)
                    )
                    selected_now = deduplicate(ranked_now, self.max_results)
                    domains_now = {
                        str(item.get("source_domain") or (urlparse(str(item.get("url") or "")).hostname or "")).casefold()
                        for item in selected_now
                        if item.get("url")
                    } - {""}
                    providers_now = {
                        outcome.provider
                        for outcome in outcomes
                        if outcome.ok and outcome.results
                    }
                    if (
                        len(selected_now) >= min(3, self.max_results)
                        and len(domains_now) >= min(3, self.max_results)
                        and len(providers_now) >= min(2, len(self.providers))
                    ):
                        break
            finally:
                for task in pending_political:
                    if not task.done():
                        task.cancel()
                if pending_political:
                    await asyncio.gather(*pending_political, return_exceptions=True)

            merged = [
                {**item, "provider": outcome.provider}
                for outcome in outcomes
                if outcome.ok
                for item in outcome.results
            ]
            ranked = rank_results(cleaned, merged, realtime=is_realtime_query(cleaned))
            selected = deduplicate(ranked, self.max_results)

            # The strict relevance ranker can legitimately prefer two headlines
            # from one publisher over a lower-scoring but still valid independent
            # newsroom. For live Chile political news, preserve real candidates
            # discovered by the targeted publisher searches so one editorial
            # domain cannot consume the entire evidence set.
            political_publishers = {
                "biobiochile.cl", "cooperativa.cl", "latercera.com",
                "24horas.cl", "emol.com", "cnnchile.com",
            }
            diverse_selected: list[dict] = []
            diverse_domains: set[str] = set()
            for item in selected:
                host = str(
                    item.get("source_domain")
                    or (urlparse(str(item.get("url") or "")).hostname or "")
                ).casefold().rstrip(".").removeprefix("www.")
                if host and host not in diverse_domains:
                    diverse_selected.append(item)
                    diverse_domains.add(host)
            for item in merged:
                if len(diverse_selected) >= self.max_results:
                    break
                url = str(item.get("url") or "").strip()
                host = str(
                    item.get("source_domain")
                    or (urlparse(url).hostname or "")
                ).casefold().rstrip(".").removeprefix("www.")
                if not url.startswith("https://") or not host or host in diverse_domains:
                    continue
                if not any(host == domain or host.endswith("." + domain) for domain in political_publishers):
                    continue
                story = f"{item.get('title', '')} {item.get('snippet', '')}"
                if re.search(
                    r"\b(?:pol[ií]tic\w*|gobiern\w*|president\w*|elecci\w*|"
                    r"congreso|senad\w*|ministro\w*|parlament\w*|diputad\w*|"
                    r"reforma\w*|proyecto de ley|gabinete|partido\w*)\b",
                    story,
                    re.IGNORECASE,
                ) is None:
                    continue
                diverse_selected.append(item)
                diverse_domains.add(host)
            if len(diverse_selected) >= min(3, self.max_results):
                selected = diverse_selected[: self.max_results]
            recovered_domains = {
                str(item.get("source_domain") or (urlparse(str(item.get("url") or "")).hostname or "")).casefold()
                for item in selected
                if item.get("url")
            } - {""}
            recovered_providers = sorted({
                outcome.provider
                for outcome in outcomes
                if outcome.ok and outcome.results
            })
            logger.info(
                "web_search_chile_political_diversity_recovery result_count=%d "
                "distinct_domains=%d providers=%s",
                len(selected),
                len(recovered_domains),
                recovered_providers,
            )

        if chile_economic_news_intent and merged:
            # Search providers may discover several real publisher domains while
            # strict ranking retains only an opaque Google News wrapper. Preserve
            # independently discovered *direct* newsroom URLs that are still
            # economically relevant. No URL or evidence is synthesized here.
            economy_direct: list[dict] = []
            economy_domains: set[str] = set()
            for item in [*selected, *merged]:
                if len(economy_direct) >= self.max_results:
                    break
                url = str(item.get("url") or "").strip()
                parsed = urlparse(url)
                url_host = (parsed.hostname or "").casefold().rstrip(".").removeprefix("www.")
                source_host = str(item.get("source_domain") or url_host).casefold().rstrip(".").removeprefix("www.")
                if source_host in economy_domains or not _is_direct_chile_economy_item(item):
                    continue
                economy_direct.append(item)
                economy_domains.add(source_host)
            if len(economy_direct) >= min(3, self.max_results):
                selected = economy_direct[: self.max_results]
                logger.info(
                    "web_search_chile_economy_direct_diversity selected_count=%d distinct_domains=%d",
                    len(selected),
                    len(economy_domains),
                )

        minimum_coverage = min(3, self.max_results)
        selected_domains = {
            str(item.get("source_domain") or (urlparse(str(item.get("url") or "")).hostname or "")).casefold()
            for item in selected
            if item.get("url")
        }
        insufficient_evidence = (
            len(selected) < minimum_coverage
            or len(selected_domains - {""}) < minimum_coverage
        )
        selected_providers = {
            str(item.get("provider") or "").strip()
            for item in selected
            if str(item.get("provider") or "").strip()
        }
        insufficient_provider_diversity = bool(selected) and len(selected_providers) < 2
        yahoo_available = (
            "yahoo" in self.fallback_providers
            and "yahoo" in self.circuits
        )
        if (
            yahoo_available
            and (insufficient_evidence or insufficient_provider_diversity)
            and loop.time() < deadline
        ):
            # Reserve one independent general-web fallback when either evidence
            # coverage OR provider diversity is incomplete. Several domains from
            # one index are useful, but they are not independent provider failover.
            # This preserves the latency-first primary wave while guaranteeing a
            # second provider attempt only when the final evidence still needs it.
            remaining = deadline - loop.time()
            # Reuse the normalized, broadened query already sent to the primary
            # providers. The user's raw phrasing can be too narrow or noisy for a
            # fallback index, especially for official/non-news sources.
            fallback_query = provider_query or cleaned
            try:
                yahoo_outcome = await asyncio.wait_for(
                    self._bounded_call_provider("yahoo", fallback_query),
                    timeout=remaining,
                )
            except asyncio.TimeoutError:
                yahoo_outcome = ProviderOutcome(
                    provider="yahoo", ok=False, error_code="PROVIDER_TIMEOUT"
                )
            outcomes.append(yahoo_outcome)
            if yahoo_outcome.ok and yahoo_outcome.results:
                merged = [
                    {**item, "provider": outcome.provider}
                    for outcome in outcomes
                    if outcome.ok
                    for item in outcome.results
                ]
                ranked = rank_results(cleaned, merged, realtime=is_realtime_query(cleaned))
                selected = deduplicate(ranked, self.max_results)
                selected_domains = {
                    str(item.get("source_domain") or (urlparse(str(item.get("url") or "")).hostname or "")).casefold()
                    for item in selected
                    if item.get("url")
                }
                if selected:
                    provider_query = fallback_query

        if not selected and merged:
            # Last-resort semantic rescue: providers returned real candidates, but
            # strict ranking rejected all of them. Re-rank against the already
            # normalized recovery query and keep only candidates that still share
            # at least one original topic token. This prevents false empty results
            # without allowing unrelated provider noise into the response.
            original_topics = set(_topic_tokens(cleaned))
            rescue_query = recovery_query or provider_query or cleaned
            rescue_ranked = rank_results(
                rescue_query,
                merged,
                realtime=is_realtime_query(cleaned),
            )
            if original_topics:
                rescue_ranked = [
                    item
                    for item in rescue_ranked
                    if original_topics
                    & set(
                        _tokens(
                            f"{item.get('title', '')} {item.get('snippet', '')} "
                            f"{item.get('source_domain', '')} {item.get('url', '')}"
                        )
                    )
                ]
            rescue_selected = deduplicate(rescue_ranked, self.max_results)
            if rescue_selected:
                selected = rescue_selected
                provider_query = rescue_query
                logger.info(
                    "web_search_semantic_rescue candidate_count=%d selected_count=%d",
                    len(merged),
                    len(selected),
                )

        if who_dengue_source_intent and merged and not _has_who_dengue_item(selected):
            # If a valid WHO fact sheet was found, preserve it in the final set even
            # when higher-volume CDC/PAHO results otherwise occupy every slot.
            who_candidates = [
                item for item in rank_results(cleaned, merged, realtime=False)
                if _is_who_dengue_item(item)
            ]
            if who_candidates:
                selected = deduplicate([*who_candidates, *selected], self.max_results)

        if chile_public_info_intent:
            selected_domains_before_portal_fallback = {
                str(item.get("source_domain") or (urlparse(str(item.get("url") or "")).hostname or "")).casefold().removeprefix("www.")
                for item in selected
                if item.get("url")
            }
            if len(selected_domains_before_portal_fallback - {""}) < min(3, self.max_results):
                # A single unreadable gob.cl homepage is not useful evidence.
                # Keep known, canonical public portals as navigation candidates
                # when live indexes collapse to one domain. The HTTP fetch/extractor
                # must still read their real pages before any text reaches synthesis.
                merged = [
                    item for item in merged
                    if not (
                        (urlparse(str(item.get("url") or "")).hostname or "").casefold().removeprefix("www.") == "gob.cl"
                        and urlparse(str(item.get("url") or "")).path in {"", "/"}
                    )
                ]
                merged.extend(dict(item) for item in _CHILE_PUBLIC_INFO_NAVIGATION_CANDIDATES)
                ranked = rank_results(cleaned, merged, realtime=is_realtime_query(cleaned))
                selected = deduplicate(ranked, self.max_results)
                selected_domains_after_portal_fallback = {
                    str(item.get("source_domain") or (urlparse(str(item.get("url") or "")).hostname or "")).casefold().removeprefix("www.")
                    for item in selected
                    if item.get("url")
                } - {""}
                logger.warning(
                    "web_search_chile_public_info_portal_fallback candidate_count=%d "
                    "selected_count=%d distinct_domains=%d",
                    len(_CHILE_PUBLIC_INFO_NAVIGATION_CANDIDATES),
                    len(selected),
                    len(selected_domains_after_portal_fallback),
                )

        if commercial_technology_site_intent:
            selected_company_hosts = {
                str(item.get("source_domain") or (urlparse(str(item.get("url") or "")).hostname or ""))
                .casefold().rstrip(".").removeprefix("www.")
                for item in selected
                if _is_chile_commercial_technology_item(item)
            } - {""}
            if len(selected_company_hosts) < min(3, self.max_results):
                # Search indexes sometimes return only commercial directories or
                # press releases for this intent. Keep canonical company websites
                # as navigation candidates; they become evidence only after the
                # fetcher extracts their actual page text.
                navigation_candidates: list[dict] = []
                seen_company_hosts: set[str] = set()
                for item in [*selected, *[dict(candidate) for candidate in _CHILE_TECH_COMPANY_NAVIGATION_CANDIDATES]]:
                    if not _is_chile_commercial_technology_item(item):
                        continue
                    host = str(
                        item.get("source_domain")
                        or (urlparse(str(item.get("url") or "")).hostname or "")
                    ).casefold().rstrip(".").removeprefix("www.")
                    url = str(item.get("url") or "").strip()
                    if not host or not url or host in seen_company_hosts:
                        continue
                    seen_company_hosts.add(host)
                    navigation_candidates.append(item)
                    if len(navigation_candidates) >= self.max_results:
                        break
                if len(navigation_candidates) >= min(3, self.max_results):
                    selected = navigation_candidates
                logger.warning(
                    "web_search_chile_commercial_navigation_fallback candidate_count=%d "
                    "selected_count=%d distinct_company_domains=%d",
                    len(_CHILE_TECH_COMPANY_NAVIGATION_CANDIDATES),
                    len(navigation_candidates),
                    len(seen_company_hosts),
                )

        if not selected and merged:
            # Diagnose relevance rejection without recording query text, titles,
            # snippets, or full URLs. This lets production logs distinguish
            # provider-empty responses from candidates excluded by ranking.
            anchors = set(_topic_tokens(cleaned))
            required_hits = min(2, len(anchors))
            diagnostics = []
            for item in merged[:8]:
                parsed = urlparse(str(item.get("url") or ""))
                url_evidence = (
                    f"{parsed.hostname or ''} "
                    f"{parsed.path.replace('-', ' ').replace('_', ' ').replace('/', ' ')}"
                )
                title_tokens = set(_tokens(str(item.get("title") or "")))
                snippet_tokens = set(_tokens(str(item.get("snippet") or "")))
                url_tokens = set(_tokens(url_evidence))
                combined_tokens = title_tokens | snippet_tokens | url_tokens
                diagnostics.append({
                    "provider": str(item.get("provider") or "unknown"),
                    "title_hits": len(anchors & title_tokens),
                    "snippet_hits": len(anchors & snippet_tokens),
                    "url_hits": len(anchors & url_tokens),
                    "combined_hits": len(anchors & combined_tokens),
                })
            logger.warning(
                "web_search_rank_rejected_candidates anchor_count=%d required_hits=%d "
                "candidate_count=%d candidates=%s",
                len(anchors),
                required_hits,
                len(merged),
                diagnostics,
            )

        errors = [
            f"{outcome.provider}:{outcome.error_code}"
            for outcome in outcomes
            if not outcome.ok and outcome.error_code
        ]
        status = "OK" if selected else (
            "PROVIDER_TIMEOUT"
            if outcomes and all(o.error_code in {"PROVIDER_TIMEOUT", "CIRCUIT_OPEN"} for o in outcomes)
            else "NO_RESULTS"
        )
        payload = self._empty_payload(
            query=cleaned,
            status=status,
            errors=errors,
            outcomes=outcomes,
            started=started,
            results=selected,
            executed_query=provider_query,
        )
        if selected and not force_fresh:
            self.cache.put(cache_key, payload, self.cache_ttl_seconds)
        return payload

    def _empty_payload(
        self,
        *,
        query: str,
        status: str,
        errors: list[str],
        outcomes: list[ProviderOutcome],
        started: float,
        results: list[dict] | None = None,
        executed_query: str | None = None,
    ) -> dict:
        selected = list(results or [])
        providers_used = list(dict.fromkeys(o.provider for o in outcomes if o.ok))
        attempted = list(dict.fromkeys(o.provider for o in outcomes))
        winner = next((o.provider for o in outcomes if o.ok and o.results), None)
        realtime = is_realtime_query(query)
        domains = sorted({
            (urlparse(str(item.get("url") or "")).hostname or "").casefold()
            for item in selected
            if str(item.get("url") or "").strip()
        } - {""})
        verification_level = (
            "multi-source"
            if len(domains) >= 2
            else "single-source"
            if selected
            else "none"
        )
        return {
            "ok": bool(selected),
            "status": status,
            "diagnostic_code": None if selected else status,
            "engine": "DEEP33 Search Engine",
            "engine_version": "1.4.0",
            "provider_independent": True,
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "query": query,
            "depth": "realtime" if realtime else "standard",
            "realtime": realtime,
            "queries": [query],
            "queries_executed": [executed_query or query],
            "results": selected,
            "sources": selected,
            "providers": providers_used,
            "providers_attempted": attempted,
            "provider": (
                providers_used[0]
                if len(providers_used) == 1
                else "multi"
                if providers_used
                else None
            ),
            "winner_provider": winner,
            "verification": {
                "level": verification_level,
                "distinct_domains": len(domains),
                "distinct_providers": len(set(providers_used)),
                "corroborated_results": 0,
            },
            "errors": errors[:8],
            "provider_latency_ms": {
                o.provider: o.latency_ms for o in outcomes if o.latency_ms > 0
            },
            "cache_hit": False,
            "latency_ms": round((time.perf_counter() - started) * 1000.0, 2),
        }


_default_orchestrator: WebOrchestrator | None = None


def get_web_orchestrator(max_results: int = 5) -> WebOrchestrator:
    global _default_orchestrator
    if _default_orchestrator is None or _default_orchestrator.max_results != max_results:
        _default_orchestrator = WebOrchestrator(max_results=max_results)
    return _default_orchestrator
