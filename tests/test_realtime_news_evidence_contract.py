from datetime import datetime, timedelta, timezone

from backend.main import _is_publishable_realtime_news_source


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




def test_current_news_rejects_admin_template_even_with_fresh_wrapper_timestamp():
    now = datetime.now(timezone.utc).isoformat()
    wrapper = "https://news.google.com/rss/articles/opaque-emol-wrapper"
    source = _news_source(
        published_at=now,
        url=wrapper,
    )
    source["title"] = (
        "Formalizan a 60 detenidos por robo de cobre en El Teniente - Emol"
    )
    page = _fetched_page(
        published_at=None,
        url="https://neomedia.emol.com/admin/au_creatives/422/emol_template",
        title="Emol - plantilla de publicidad",
    )
    page["text"] = ("Panel administrativo y plantilla publicitaria de Emol. " * 15)

    assert not _is_publishable_realtime_news_source(
        "últimas noticias de Chile hoy",
        source,
        page,
    )


def test_current_news_does_not_inherit_timestamp_for_unmatched_redirect():
    now = datetime.now(timezone.utc).isoformat()
    source = _news_source(
        published_at=now,
        url="https://news.google.com/rss/articles/opaque-article-wrapper",
    )
    source["title"] = (
        "Formalizan a 60 detenidos por robo de cobre en El Teniente - Emol"
    )
    page = _fetched_page(
        published_at=None,
        url="https://www.emol.com/2026/10/25/promociones-y-publicidad.html",
        title="Promociones y publicidad para clientes",
    )

    assert not _is_publishable_realtime_news_source(
        "últimas noticias de Chile hoy",
        source,
        page,
    )


def test_current_news_rejects_localized_homepage_with_fresh_search_timestamp():
    now = datetime.now(timezone.utc).isoformat()
    url = "https://chile.travel/en/"
    source = {
        "title": "Home - Chile Travel",
        "url": url,
        "source_domain": "chile.travel",
        "provider": "google_news",
        "published_at": now,
        "snippet": "Explore the best destinations and experiences in Chile.",
    }
    page = _fetched_page(
        published_at=None,
        url=url,
        title="Home - Chile Travel",
    )
    page["domain"] = "chile.travel"
    page["text"] = (
        "Explore Chile: discover destinations, travel guides, landscapes, "
        "experiences, attractions and places to visit throughout the country. "
    ) * 10

    assert not _is_publishable_realtime_news_source(
        "últimas noticias de Chile hoy",
        source,
        page,
    )


def test_current_news_rejects_evergreen_chile_guide_with_fresh_search_timestamp():
    now = datetime.now(timezone.utc).isoformat()
    url = "https://dschile.cl/conoce-chile"
    source = {
        "title": "Conoce Chile - DSChile",
        "url": url,
        "source_domain": "dschile.cl",
        "provider": "google_news",
        "published_at": now,
        "snippet": "Información general para conocer Chile.",
    }
    page = _fetched_page(
        published_at=None,
        url=url,
        title="Conoce Chile - DSChile",
    )
    page["domain"] = "dschile.cl"
    page["text"] = (
        "Conoce Chile, su territorio, sus regiones, cultura, historia, "
        "destinos turísticos y datos generales para preparar una visita. "
    ) * 10

    assert not _is_publishable_realtime_news_source(
        "últimas noticias de Chile hoy",
        source,
        page,
    )


def test_current_news_requires_title_identity_even_for_same_direct_url():
    now = datetime.now(timezone.utc).isoformat()
    url = "https://noticias.example/2026/10/25/gobierno-medidas-chile.html"
    source = _news_source(published_at=now, url=url)
    page = _fetched_page(
        published_at=None,
        url=url,
        title="Conoce Chile: regiones, cultura y destinos turísticos",
    )
    page["text"] = (
        "Guía turística de Chile con regiones, cultura, destinos y datos "
        "generales para visitantes. "
    ) * 10

    assert not _is_publishable_realtime_news_source(
        "últimas noticias de Chile hoy",
        source,
        page,
    )
