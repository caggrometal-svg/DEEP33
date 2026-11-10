from datetime import datetime, timedelta, timezone

from backend.main import _compact_model_web_evidence, _compact_web_synthesis_payload, _is_publishable_realtime_news_source, _web_search_result_limit


def _news_source(*, published_at, url="https://noticias.example/2026/10/25/gobierno-medidas-chile.html"):
    return {
        "title": "Últimas noticias de Chile: Gobierno anuncia nuevas medidas hoy",
        "url": url,
        "snippet": "El Gobierno de Chile anunció nuevas medidas hoy y confirmó los cambios.",
        "source_domain": "noticias.example",
        "provider": "bing",
        "published_at": published_at,
    }


def _fetched_page(*, published_at, url="https://noticias.example/2026/10/25/gobierno-medidas-chile.html", title=None):
    return {
        "title": title or "Gobierno anuncia nuevas medidas en Chile hoy",
        "url": url,
        "domain": "noticias.example",
        "published_at": published_at,
        "text": (
            "Últimas noticias de Chile. El Gobierno anunció nuevas medidas hoy. "
            "El comunicado confirma los cambios y entrega detalles de la medida. "
        ) * 5,
    }


def test_current_news_accepts_recent_relevant_extracted_article():
    now = datetime.now(timezone.utc).isoformat()
    source = _news_source(published_at=now)
    page = _fetched_page(published_at=now)

    assert _is_publishable_realtime_news_source(
        "últimas noticias de Chile hoy",
        source,
        page,
    )


def test_current_news_rejects_undated_evergreen_geography_page():
    source = _news_source(
        published_at=None,
        url="https://paises.example/america/chile",
    )
    page = _fetched_page(
        published_at=None,
        url="https://paises.example/america/chile",
        title="Chile: capital, mapa, bandera, población y datos",
    )
    page["text"] = (
        "Chile es un país de América del Sur. Su capital es Santiago. "
        "La geografía y la población del país se describen en esta página. "
    ) * 8

    assert not _is_publishable_realtime_news_source(
        "últimas noticias de Chile hoy",
        source,
        page,
    )


def test_current_news_rejects_stale_extracted_article():
    stale = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    source = _news_source(published_at=stale)
    page = _fetched_page(published_at=stale)

    assert not _is_publishable_realtime_news_source(
        "últimas noticias de Chile hoy",
        source,
        page,
    )


def test_current_news_rejects_timestamped_news_landing_page():
    now = datetime.now(timezone.utc).isoformat()
    url = "https://www.meganoticias.cl/lo-ultimo"
    source = _news_source(published_at=now, url=url)
    page = _fetched_page(
        published_at=now,
        url=url,
        title="Últimas noticias de Chile y el mundo hoy",
    )

    assert not _is_publishable_realtime_news_source(
        "últimas noticias de Chile hoy",
        source,
        page,
    )


def test_current_news_rejects_recent_dated_landing_page():
    now = datetime.now(timezone.utc).isoformat()
    url = "https://www.clarin.com/ultimo-momento"
    source = _news_source(published_at=now, url=url)
    page = _fetched_page(
        published_at=now,
        url=url,
        title="Últimas noticias de economía, Chile y el mundo",
    )

    assert not _is_publishable_realtime_news_source(
        "últimas noticias de Chile hoy",
        source,
        page,
    )


def test_current_news_rejects_unresolved_google_news_wrapper():
    now = datetime.now(timezone.utc).isoformat()
    url = "https://news.google.com/rss/articles/current-signed-wrapper"
    source = _news_source(published_at=now, url=url)
    page = _fetched_page(
        published_at=now,
        url=url,
        title="Últimas noticias de Chile hoy",
    )

    assert not _is_publishable_realtime_news_source(
        "últimas noticias de Chile hoy",
        source,
        page,
    )



def test_current_news_relevance_ignores_chat_output_instructions():
    now = datetime.now(timezone.utc).isoformat()
    source = _news_source(published_at=now)
    page = _fetched_page(published_at=now)
    chat_prompt = (
        "Busca las últimas noticias de Chile hoy. "
        "Resume únicamente hechos respaldados por tres artículos recientes "
        "de tres medios distintos y termina con NEWS_STREAM_PASS."
    )

    assert _is_publishable_realtime_news_source(
        chat_prompt,
        source,
        page,
    )


def test_news_fetch_policy_allows_eight_attempts_to_reach_three_valid_articles():
    from backend.search.web_pipeline import FetchPolicy

    policy = FetchPolicy(
        target_sources=3,
        max_sources=8,
        global_budget_seconds=8.5,
        concurrency=5,
    )

    assert policy.target_sources == 3
    assert policy.max_sources == 8
    assert policy.global_budget_seconds == 8.5


def test_strict_chile_news_uses_expanded_candidate_pool():
    assert _web_search_result_limit("Busca las últimas noticias de Chile hoy", 5) >= 12
    assert _web_search_result_limit("documentación oficial de Python", 5) == 5


def test_strict_news_model_context_is_compact_but_keeps_full_quorum_metadata():
    pages = [
        {
            "title": f"Artículo {index}",
            "url": f"https://medio{index}.example/noticia",
            "text": ("contenido comprobable " * 200),
            "published_at": "2026-11-10T02:00:00Z",
        }
        for index in range(1, 5)
    ]
    evidence = {
        "fetched_pages": pages,
        "search_results": [{"title": f"Artículo {index}"} for index in range(1, 5)],
        "domains_used": 4,
        "news_quorum": {
            "status": "OK",
            "fetched": 4,
            "domains": 4,
            "required": 3,
        },
        "extracted_text_chars": sum(len(page["text"]) for page in pages),
    }

    compact = _compact_model_web_evidence(evidence, strict_news=True)

    assert len(compact["fetched_pages"]) == 3
    assert len(compact["search_results"]) == 3
    assert all(len(page["text"]) <= 500 for page in compact["fetched_pages"])
    assert set(compact["fetched_pages"][0]) == {"title", "url", "domain", "published_at", "text"}
    assert all(len(item.get("snippet", "")) <= 160 for item in compact["search_results"])
    assert compact["domains_used"] == 4
    assert compact["news_quorum"] == evidence["news_quorum"]
    assert compact["extracted_text_chars"] == evidence["extracted_text_chars"]
    assert compact["model_context_compacted"] is True


def test_non_news_model_evidence_is_not_truncated():
    evidence = {
        "fetched_pages": [{"text": "x" * 2500}],
        "search_results": [{"title": "A"}, {"title": "B"}, {"title": "C"}, {"title": "D"}],
        "domains_used": 4,
    }

    compact = _compact_model_web_evidence(evidence, strict_news=False)

    assert compact == evidence


def test_news_synthesis_fallback_payload_is_bounded_for_free_inference():
    import json

    evidence = {
        "realtime": True,
        "retrieved_at": "2026-11-10T03:00:00Z",
        "verification": {"provider_quorum_met": True, "distinct_providers": 2},
        "news_quorum": {"required": True, "met": True, "articles": 3, "domains": 3},
        "fetched_pages": [
            {
                "title": f"Noticia {index}",
                "url": f"https://medio{index}.example/noticia",
                "domain": f"medio{index}.example",
                "published_at": "2026-11-10T02:00:00Z",
                "text": "Hechos verificados y contexto periodístico. " * 80,
            }
            for index in range(1, 4)
        ],
        "search_results": [],
    }
    payload = {
        "max_tokens": 1200,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Server-side web evidence for this request follows. "
                    + json.dumps(evidence, ensure_ascii=False)
                ),
            },
            {"role": "user", "content": "Resume las noticias."},
        ],
    }

    compact = _compact_web_synthesis_payload(payload)

    assert compact is not None
    assert compact["max_tokens"] == 384
    compact_evidence = json.loads(
        compact["messages"][0]["content"].split(
            "Server-side web evidence for this request follows.", 1
        )[1]
    )
    assert len(compact_evidence["fetched_pages"]) == 3
    assert all(len(page["text"]) <= 450 for page in compact_evidence["fetched_pages"])
    assert compact_evidence["news_quorum"]["met"] is True



def test_current_news_never_inherits_recent_serp_timestamp_when_page_has_no_date():
    now = datetime.now(timezone.utc).isoformat()
    source = _news_source(published_at=now)
    page = _fetched_page(published_at=None)

    assert not _is_publishable_realtime_news_source(
        "últimas noticias de Chile hoy",
        source,
        page,
    )


def test_current_news_rejects_additional_news_index_landing_path_even_with_page_date():
    now = datetime.now(timezone.utc).isoformat()
    url = "https://www.df.cl/ultimas"
    source = _news_source(published_at=now, url=url)
    page = _fetched_page(published_at=now, url=url, title="Últimas noticias")

    assert not _is_publishable_realtime_news_source(
        "últimas noticias de Chile hoy",
        source,
        page,
    )


def test_html_fetch_extracts_article_published_time_meta():
    from tools.web_fetch import _extract_text

    body = (
        "El Gobierno informó nuevas medidas para el país y detalló el calendario "
        "de implementación, las instituciones responsables y los próximos pasos. "
    ) * 5
    html_doc = (
        '<html><head><meta property="article:published_time" '
        'content="2026-11-10T02:15:00Z"><title>Medidas anunciadas</title></head>'
        f"<body><article><p>{body}</p></article></body></html>"
    )
    title, text, published_at = _extract_text(
        html_doc.encode("utf-8"), "text/html; charset=utf-8", 5000,
        include_metadata=True,
    )
    assert title == "Medidas anunciadas"
    assert len(text) >= 80
    assert published_at == "2026-11-10T02:15:00Z"


def test_html_fetch_extracts_date_published_from_news_article_jsonld():
    from tools.web_fetch import _extract_text
    import json

    body = (
        "La autoridad publicó el informe y confirmó los resultados del proceso "
        "con detalles sobre las medidas, los plazos y la implementación nacional. "
    ) * 5
    structured = {
        "@context": "https://schema.org",
        "@type": "NewsArticle",
        "headline": "Informe oficial publicado",
        "datePublished": "2026-11-10T01:45:00Z",
        "articleBody": body,
    }
    html_doc = (
        '<html><head><script type="application/ld+json">'
        + json.dumps(structured)
        + "</script></head><body></body></html>"
    )
    title, text, published_at = _extract_text(
        html_doc.encode("utf-8"), "text/html; charset=utf-8", 5000,
        include_metadata=True,
    )
    assert title == "Informe oficial publicado"
    assert len(text) >= 80
    assert published_at == "2026-11-10T01:45:00Z"
