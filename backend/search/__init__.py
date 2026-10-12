"""DEEP33 search engine package."""

from .engine import SearchEngine, SearchPlan
from .resilient import WebOrchestrator, get_web_orchestrator
from .web_pipeline import canonicalize_url, fetch_many, fetch_one, rank_evidence

__all__ = [
    "SearchEngine",
    "SearchPlan",
    "WebOrchestrator",
    "get_web_orchestrator",
    "canonicalize_url",
    "fetch_many",
    "fetch_one",
    "rank_evidence",
]
