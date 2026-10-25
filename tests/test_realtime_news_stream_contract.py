import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient

from backend import main


client = TestClient(main.app)


class _FakeMemory:
    enabled = False


def _fresh_stamp():
    return datetime.now(timezone.utc).isoformat()


def _news_result(title, url, domain, provider, published_at):
    return {
        "title": title,
        "url": url,
        "source_domain": domain,
        "provider": provider,
        "published_at": published_at,
        "snippet": (
            "Noticias de Chile publicadas hoy: información reciente y "
            "detalles confirmados del acontecimiento nacional."
        ),
    }


def test_chat_stream_blocks_current_news_when_only_two_verified_articles(monkeypatch):
    """Reproduce a fresh Google News item resolving to an Emol admin template."""
    now = _fresh_stamp()
    query = (
        "Busca en Internet las últimas noticias de Chile publicadas hoy; "
        "verifica tres fuentes independientes."
    )
    url_a = "https://noticias-a.example/2026/10/25/gobierno-medidas-chile.html"
    url_b = "https://noticias-b.example/2026/10/25/congreso-medidas-chile.html"
    wrapper = "https://news.google.com/rss/articles/opaque-emol-wrapper"
    results = [
        _news_result(
            "Gobierno anuncia nuevas medidas en Chile hoy",
            url_a,
            "noticias-a.example",
            "bing",
            now,
        ),
        _news_result(
            "Congreso anuncia nuevas medidas en Chile hoy",
            url_b,
            "noticias-b.example",
            "google_news",
            now,
        ),
        _news_result(
            "Formalizan a 60 detenidos por robo de cobre en El Teniente - Emol",
            wrapper,
            "emol.com",
            "bing",
            now,
        ),
    ]
    search_calls = []
    gateway_calls = []

    async def fake_search(_query, **_kwargs):
        search_calls.append(_query)
        return {
            # Simulate a successful SERP containing two direct articles and
            # one fresh-dated Google News wrapper that resolves to an admin template.
            # The downstream article validator must reject the template, leaving
            # fewer than three verified articles and blocking model synthesis.
            "ok": True,
            "fresh_request": True,
            "retrieved_at": now,
            "query": _query,
            "results": results,
            "providers": ["bing", "google_news"],
            "providers_attempted": ["bing", "google_news"],
            "verification": {
                "level": "multi-source",
                "distinct_domains": 3,
                "distinct_providers": 2,
                "corroborated_results": 0,
            },
        }

    async def fake_fetch_source(source, **_kwargs):
        if source["url"] == url_a:
            return {
                "title": "Gobierno anuncia nuevas medidas en Chile hoy",
                "url": url_a,
                "domain": "noticias-a.example",
                "provider": "bing",
                "published_at": None,
                "text": (
                    "El Gobierno anunció nuevas medidas en Chile hoy. "
                    "La información nacional incluye detalles verificables "
                    "del anuncio y sus principales consecuencias. "
                ) * 10,
                "text_chars": 1200,
            }
        if source["url"] == url_b:
            return {
                "title": "Congreso anuncia nuevas medidas en Chile hoy",
                "url": url_b,
                "domain": "noticias-b.example",
                "provider": "google_news",
                "published_at": None,
                "text": (
                    "El Congreso anunció nuevas medidas en Chile hoy. "
                    "El reporte describe la decisión, sus antecedentes y "
                    "los datos confirmados del acontecimiento. "
                ) * 10,
                "text_chars": 1200,
            }
        assert source["url"] == wrapper
        return {
            "title": "Emol - plantilla de publicidad",
            "url": "https://neomedia.emol.com/admin/au_creatives/422/emol_template",
            "domain": "neomedia.emol.com",
            "provider": "bing",
            "published_at": None,
            "text": ("Panel administrativo y plantilla publicitaria de Emol. " * 20),
            "text_chars": 1200,
            "title_from_page": True,
        }

    async def fake_enforce_controls(*_args, **_kwargs):
        return None

    async def fake_prepare_messages(request, *_args, **_kwargs):
        return ([message.model_dump() for message in request.messages], "NEUTRO")

    async def fake_claim(*_args, **_kwargs):
        return "CLAIMED", {"lease_token": "test-lease"}

    async def fake_finalize(*_args, **_kwargs):
        return None

    async def forbidden_stream_gateway(*_args, **_kwargs):
        gateway_calls.append(True)
        yield b'data: {"choices":[{"delta":{"content":"MODELO_NO_DEBE_RESPONDER"}}]}\n\n'
        yield b"data: [DONE]\n\n"

    monkeypatch.setattr(main, "memory", _FakeMemory())
    monkeypatch.setattr(main, "enforce_client_controls", fake_enforce_controls)
    monkeypatch.setattr(main, "prepare_messages", fake_prepare_messages)
    monkeypatch.setattr(main, "shared_idempotency_claim", fake_claim)
    monkeypatch.setattr(main, "_finalize_stream", fake_finalize)
    monkeypatch.setattr(main, "search_web", fake_search)
    monkeypatch.setattr(main, "_fetch_web_source", fake_fetch_source)
    monkeypatch.setattr(main, "score_result", lambda *_args, **_kwargs: 1.0)
    monkeypatch.setattr(main, "stream_gateway", forbidden_stream_gateway)

    with client.stream(
        "POST",
        "/v1/chat/stream",
        json={
            "messages": [{"role": "user", "content": query}],
            "personality": "NEUTRO",
        },
        headers={
            "X-DEEP33-Session-Id": "current-news-quorum-regression",
            "X-Idempotency-Key": "current-news-quorum-regression-key",
        },
    ) as response:
        body = "".join(response.iter_text())

    assert response.status_code == 200
    assert search_calls, "The integration test must execute the live-search preparation path."
    assert not gateway_calls, "The model must not run when the three-article quorum fails."
    assert "MODELO_NO_DEBE_RESPONDER" not in body
    assert '"sources":0' in body
    assert "emol_template" not in body
    assert "verified_current_news_article" not in body
