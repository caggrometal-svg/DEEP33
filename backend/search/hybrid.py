from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Any

import httpx

from backend.search.chunking import chunk_document


ENGINE_VERSION = "1.1.0"
_TOKEN_RE = re.compile(r"[\\wáéíóúüñÁÉÍÓÚÜÑ]{2,}", re.UNICODE)


class HybridSearchUnavailableError(RuntimeError):
    pass


def _tokens(text: str) -> set[str]:
    return {token.lower() for token in _TOKEN_RE.findall(text or "")}


def _vector_literal(values: list[float]) -> str:
    return "[" + ",".join(format(float(value), ".9g") for value in values) + "]"


def _freshness_bonus(metadata: dict[str, Any]) -> float:
    raw = str(metadata.get("published_at") or "").strip()
    if not raw:
        return 0.0
    try:
        published = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        age_days = max(
            0.0,
            (datetime.now(timezone.utc) - published.astimezone(timezone.utc)).total_seconds() / 86400,
        )
        return max(0.0, 1.0 - min(age_days / 365.0, 1.0))
    except ValueError:
        return 0.0


def rerank_results(query: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    query_tokens = _tokens(query)
    output: list[dict[str, Any]] = []

    for row in rows:
        content = str(row.get("content") or "")
        metadata = row.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        searchable = " ".join(
            [
                content,
                str(metadata.get("title") or ""),
                str(metadata.get("tags") or ""),
            ]
        )
        coverage = (
            len(query_tokens.intersection(_tokens(searchable))) / len(query_tokens)
            if query_tokens
            else 0.0
        )
        rrf = float(row.get("rrf_score") or 0.0)
        rrf_normalized = min(1.0, rrf * 60.0)
        freshness = _freshness_bonus(metadata)
        rerank_score = (
            rrf_normalized * 0.70
            + coverage * 0.20
            + freshness * 0.05
            + (0.05 if metadata.get("title") else 0.0)
        )
        output.append(
            {
                **row,
                "rerank_score": round(rerank_score, 6),
                "keyword_coverage": round(coverage, 6),
            }
        )

    output.sort(
        key=lambda item: (
            float(item.get("rerank_score") or 0.0),
            float(item.get("rrf_score") or 0.0),
            float(item.get("vector_score") or 0.0),
            float(item.get("keyword_score") or 0.0),
        ),
        reverse=True,
    )
    return output


class HybridSearchClient:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout_seconds: float | None = None,
        dimensions: int | None = None,
    ) -> None:
        self.base_url = (
            base_url if base_url is not None else os.getenv("DEEP33_KNOWLEDGE_DB_URL", "")
        ).strip().rstrip("/")
        self.api_key = (
            api_key if api_key is not None else os.getenv("DEEP33_KNOWLEDGE_DB_KEY", "")
        ).strip()
        self.timeout_seconds = max(
            3.0,
            min(
                20.0,
                float(
                    timeout_seconds
                    if timeout_seconds is not None
                    else os.getenv("DEEP33_HYBRID_SEARCH_TIMEOUT_SECONDS", "8")
                ),
            ),
        )
        self.dimensions = max(
            1,
            int(
                dimensions
                if dimensions is not None
                else os.getenv("DEEP33_VECTOR_DIMENSIONS", "1536")
            ),
        )

    @property
    def enabled(self) -> bool:
        return bool(
            os.getenv("DEEP33_HYBRID_SEARCH_ENABLED", "false").strip().lower() == "true"
            and self.base_url
            and self.api_key
        )

    def status(self) -> dict[str, Any]:
        return {
            "engine": "DEEP33 Hybrid Search",
            "engine_version": ENGINE_VERSION,
            "enabled": self.enabled,
            "configured": bool(self.base_url and self.api_key),
            "dimensions": self.dimensions,
            "transport": "supabase_postgrest_rpc",
            "vector_backend": "pgvector",
            "keyword_backend": "postgresql_tsvector",
            "fusion": "weighted_reciprocal_rank_fusion_plus_metadata_rerank",
            "schema": "deep33_knowledge_chunks",
        }

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "apikey": self.api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def _rpc(self, function_name: str, payload: dict[str, Any]) -> Any:
        if not self.enabled:
            raise HybridSearchUnavailableError("HYBRID_SEARCH_NOT_CONFIGURED")
        url = f"{self.base_url}/rest/v1/rpc/{function_name}"
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(url, json=payload, headers=self._headers())
        except httpx.HTTPError as exc:
            raise HybridSearchUnavailableError(type(exc).__name__) from exc
        if not 200 <= response.status_code < 300:
            raise HybridSearchUnavailableError(f"hybrid_http_{response.status_code}")
        try:
            return response.json()
        except ValueError as exc:
            raise HybridSearchUnavailableError("hybrid_invalid_json") from exc

    async def search(
        self,
        query: str,
        *,
        embedding: list[float] | None = None,
        limit: int = 8,
        metadata_filter: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if embedding is not None and len(embedding) != self.dimensions:
            raise ValueError(f"EMBEDDING_DIMENSIONS_REQUIRED_{self.dimensions}")
        clean_query = " ".join(query.split()).strip()
        if not clean_query and embedding is None:
            raise ValueError("HYBRID_QUERY_REQUIRED")

        raw = await self._rpc(
            "search_deep33_chunks",
            {
                "p_query": clean_query,
                "p_embedding": _vector_literal(embedding) if embedding is not None else None,
                "p_limit": max(1, min(20, int(limit))),
                "p_metadata_filter": metadata_filter or {},
                "p_rrf_k": 60,
            },
        )
        rows = raw if isinstance(raw, list) else []
        reranked = rerank_results(clean_query, rows)
        return {
            "ok": bool(reranked),
            "engine": "DEEP33 Hybrid Search",
            "engine_version": ENGINE_VERSION,
            "mode": "hybrid" if embedding is not None and clean_query else "vector" if embedding is not None else "keyword",
            "query": clean_query,
            "results": reranked,
            "sources": [
                {
                    "document_id": row.get("document_id"),
                    "chunk_index": row.get("chunk_index"),
                    "content": row.get("content"),
                    "metadata": row.get("metadata") or {},
                    "score": row.get("rerank_score"),
                    "rrf_score": row.get("rrf_score"),
                    "vector_score": row.get("vector_score"),
                    "keyword_score": row.get("keyword_score"),
                }
                for row in reranked
            ],
        }

    async def index_document(
        self,
        document_id: str,
        content: str,
        *,
        metadata: dict[str, Any] | None = None,
        embeddings: list[list[float]] | None = None,
        target_chars: int = 1400,
        overlap_chars: int = 220,
    ) -> dict[str, Any]:
        clean_id = document_id.strip()
        if not clean_id:
            raise ValueError("DOCUMENT_ID_REQUIRED")
        chunks = chunk_document(
            content,
            base_metadata=metadata or {},
            target_chars=target_chars,
            overlap_chars=overlap_chars,
        )
        if not chunks:
            raise ValueError("DOCUMENT_CONTENT_REQUIRED")
        if embeddings is not None:
            if len(embeddings) != len(chunks):
                raise ValueError("EMBEDDING_CHUNK_COUNT_MISMATCH")
            for embedding in embeddings:
                if len(embedding) != self.dimensions:
                    raise ValueError(f"EMBEDDING_DIMENSIONS_REQUIRED_{self.dimensions}")

        payload_chunks: list[dict[str, Any]] = []
        for index, chunk in enumerate(chunks):
            row = {
                "chunk_index": chunk.chunk_index,
                "content": chunk.content,
                "token_count": chunk.token_count,
                "checksum": chunk.metadata["content_sha256"],
                "metadata": chunk.metadata,
            }
            if embeddings is not None:
                row["embedding"] = _vector_literal(embeddings[index])
            payload_chunks.append(row)

        raw = await self._rpc(
            "index_deep33_chunks",
            {"p_document_id": clean_id[:200], "p_chunks": payload_chunks},
        )
        if isinstance(raw, list) and raw and isinstance(raw[0], dict):
            saved = int(raw[0].get("indexed_chunks") or len(chunks))
        elif isinstance(raw, dict):
            saved = int(raw.get("indexed_chunks") or len(chunks))
        else:
            saved = len(chunks)
        return {
            "ok": True,
            "engine": "DEEP33 Hybrid Search",
            "engine_version": ENGINE_VERSION,
            "document_id": clean_id[:200],
            "indexed_chunks": saved,
            "chunking": {
                "strategy": "paragraph_sentence_word",
                "target_chars": max(400, min(4000, int(target_chars))),
                "overlap_chars": max(0, min(target_chars // 2, int(overlap_chars))),
            },
        }
