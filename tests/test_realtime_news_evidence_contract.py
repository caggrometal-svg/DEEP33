from datetime import datetime, timedelta, timezone

from backend.main import (
    _is_publishable_realtime_news_source,
    _looks_like_realtime_news_candidate_url,
    _realtime_news_quorum,
)


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


def test_realtime_news_candidate_filter_rejects_admin_templates_and_portals():
    assert not _looks_like_realtime_news_candidate_url(
        "https://neomedia.emol.com/admin/au_creatives/422/emol_template"
    )
    assert not _looks_like_realtime_news_candidate_url(
        "https://www.infobae.com/ultimas-noticias/"
    )
    assert _looks_like_realtime_news_candidate_url(
        "https://www.latercera.com/politica/noticia/gobierno-ingresara-hoy-al-congreso/"
    )
    assert _looks_like_realtime_news_candidate_url(
        "https://news.google.com/rss/articles/example"
    )


def test_current_news_accepts_recent_relevant_extracted_article():
    now = datetime.now(timezone.utc).isoformat()
    source = _news_source(published_at=now)
    page = _fetched_page(published_at=now)

    assert _is_publishable_realtime_news_source(
        "últimas noticias de Chile hoy",
        source,
        page,
    )


def test_current_news_rejects_search_result_date_when_final_page_has_no_date():
    now = datetime.now(timezone.utc).isoformat()
    source = _news_source(published_at=now)
    page = _fetched_page(published_at=None)

    assert not _is_publishable_realtime_news_source(
        "últimas noticias de Chile hoy",
        source,
        page,
    )


def test_shared_news_quorum_requires_three_domains_and_two_providers():
    now = datetime.now(timezone.utc).isoformat()
    pages = [
        {
            "url": f"https://publisher-{index}.example/2026/10/27/article-{index}",
            "published_at": now,
            "fetched": True,
            "evidence_type": "verified_current_news_article",
        }
        for index in range(3)
    ]

    ok, domains, providers = _realtime_news_quorum(pages, ["bing", "mojeek"])
    assert ok is True
    assert len(domains) == 3
    assert len(providers) == 2

    ok_two_pages, _, _ = _realtime_news_quorum(pages[:2], ["bing", "mojeek"])
    assert ok_two_pages is False

    same_publisher = [dict(page, url="https://publisher.example/article") for page in pages]
    ok_same_domain, same_domains, _ = _realtime_news_quorum(same_publisher, ["bing", "mojeek"])
    assert ok_same_domain is False
    assert len(same_domains) == 1

    ok_single_provider, _, _ = _realtime_news_quorum(pages, ["bing"])
    assert ok_single_provider is False


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

