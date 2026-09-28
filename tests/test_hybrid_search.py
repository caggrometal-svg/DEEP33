from __future__ import annotations

import asyncio
import json
import os

import httpx

from backend.search.chunking import chunk_document
from backend.search.hybrid import HybridSearchClient, rerank_results


def test_intelligent_chunking_preserves_sentence_boundaries_and_metadata():
    text = " ".join(
        [
            "Primero explicamos el objetivo de DEEP33 con precisión.",
            "Después explicamos la arquitectura y sus dependencias.",
            "Luego describimos memoria, búsqueda híbrida y metadatos.",
            "Finalmente verificamos la integridad del documento.",
        ]
        * 8
    )
    chunks = chunk_document(
        text,
        base_metadata={"title": "DEEP33", "source_url": "https://example.org/doc"},
        target_chars=400,
        overlap_chars=40,
    )
    assert len(chunks) >= 2
    assert chunks[0].metadata["title"] == "DEEP33"
    assert chunks[0].metadata["chunk_index"] == 0
    assert chunks[0].metadata["content_sha256"]
    assert chunks[0].metadata["chunking"]["strategy"] == "paragraph_sentence_word"


def test_hybrid_reranker_fuses_rrf_and_metadata():
    rows = rerank_results(
        "deep33 internet",
        [
            {
                "document_id": "a",
                "chunk_index": 0,
                "content": "Deep33 internet architecture",
                "metadata": {"title": "DEEP33", "published_at": "2026-09-20T00:00:00Z"},
                "rrf_score": 0.016,
                "vector_score": 0.8,
                "keyword_score": 0.7,
            },
            {
                "document_id": "b",
                "chunk_index": 0,
                "content": "other",
                "metadata": {},
                "rrf_score": 0.015,
                "vector_score": 0.81,
                "keyword_score": 0.01,
            },
        ],
    )
    assert rows[0]["document_id"] == "a"
    assert rows[0]["rerank_score"] > rows[1]["rerank_score"]


def test_hybrid_status_contract_exposes_real_transport():
    previous = os.environ.get("DEEP33_HYBRID_SEARCH_ENABLED")
    os.environ["DEEP33_HYBRID_SEARCH_ENABLED"] = "true"
    try:
        status = HybridSearchClient(
            base_url="https://db.test",
            api_key="key",
            dimensions=1536,
        ).status()
        assert status["engine"] == "DEEP33 Hybrid Search"
        assert status["engine_version"] == "1.1.0"
        assert status["enabled"] is True
        assert status["configured"] is True
        assert status["dimensions"] == 1536
        assert status["transport"] == "supabase_postgrest_rpc"
        assert status["vector_backend"] == "pgvector"
        assert status["keyword_backend"] == "postgresql_tsvector"
    finally:
        if previous is None:
            os.environ.pop("DEEP33_HYBRID_SEARCH_ENABLED", None)
        else:
            os.environ["DEEP33_HYBRID_SEARCH_ENABLED"] = previous


def test_hybrid_client_sends_vector_and_keyword_query():
    seen = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json=[
                {
                    "document_id": "doc-1",
                    "chunk_index": 0,
                    "content": "DEEP33",
                    "metadata": {"title": "DEEP33"},
                    "keyword_rank": 1,
                    "vector_rank": 2,
                    "keyword_score": 0.8,
                    "vector_score": 0.9,
                    "rrf_score": 0.016,
                }
            ],
        )

    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient

    class Client(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    httpx.AsyncClient = Client
    previous = os.environ.get("DEEP33_HYBRID_SEARCH_ENABLED")
    os.environ["DEEP33_HYBRID_SEARCH_ENABLED"] = "true"
    try:
        client = HybridSearchClient(
            base_url="https://db.test",
            api_key="key",
            dimensions=3,
        )
        result = asyncio.run(
            client.search(
                "DEEP33",
                embedding=[0.1, 0.2, 0.3],
                limit=5,
                metadata_filter={"language": "es"},
            )
        )
        assert result["ok"] is True
        assert result["mode"] == "hybrid"
        assert seen["url"].endswith("/rest/v1/rpc/search_deep33_chunks")
        assert seen["payload"]["p_query"] == "DEEP33"
        assert seen["payload"]["p_metadata_filter"] == {"language": "es"}
        assert seen["payload"]["p_embedding"] == "[0.1,0.2,0.3]"
    finally:
        if previous is None:
            os.environ.pop("DEEP33_HYBRID_SEARCH_ENABLED", None)
        else:
            os.environ["DEEP33_HYBRID_SEARCH_ENABLED"] = previous
        httpx.AsyncClient = original
