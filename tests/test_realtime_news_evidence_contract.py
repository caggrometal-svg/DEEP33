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


def test_broad_chile_news_query_keeps_date_words_in_general_news_classification(monkeypatch):
    from backend import main

    query = (
        "¿Cuáles son las noticias más recientes de Chile publicadas hoy "
        "domingo 25 de octubre de 2026? Busca en Internet, verifica varias fuentes "
        "y resume los hechos principales indicando de qué fuente proviene cada información."
    )
    assert main._is_broad_current_news_query(query) is True
    assert main._is_broad_current_news_query("noticias del dólar en Chile hoy") is False


def test_broad_chile_news_accepts_moderately_relevant_real_article(monkeypatch):
    from backend import main

    now = datetime.now(timezone.utc).isoformat()
    query = (
        "¿Cuáles son las noticias más recientes de Chile publicadas hoy "
        "domingo 25 de octubre de 2026? Busca en Internet y revisa varias fuentes."
    )
    url = "https://www.futuro.cl/2026/10/25/suben-tarifas-transporte-publico/"
    source = {
        "title": "Suben las tarifas del transporte público este domingo",
        "url": url,
        "snippet": "La medida fue informada este domingo y rige desde hoy.",
        "source_domain": "futuro.cl",
        "provider": "bing",
        "published_at": now,
    }
    page = {
        "title": source["title"],
        "url": url,
        "domain": "futuro.cl",
        "published_at": now,
        "title_from_page": True,
        "text": (
            "Suben las tarifas del transporte público este domingo. "
            "La autoridad confirmó la medida y detalló su entrada en vigencia. "
        ) * 6,
    }
    monkeypatch.setattr(main, "score_result", lambda *_args, **_kwargs: 0.23)

    assert main._is_publishable_realtime_news_source(query, source, page)


def test_topical_chile_news_does_not_get_broad_query_relevance_relaxation(monkeypatch):
    from backend import main

    now = datetime.now(timezone.utc).isoformat()
    query = "Últimas noticias del dólar en Chile hoy"
    url = "https://www.futuro.cl/2026/10/25/suben-tarifas-transporte-publico/"
    source = {
        "title": "Suben las tarifas del transporte público este domingo",
        "url": url,
        "snippet": "La medida fue informada este domingo y rige desde hoy.",
        "source_domain": "futuro.cl",
        "provider": "bing",
        "published_at": now,
    }
    page = {
        "title": source["title"],
        "url": url,
        "domain": "futuro.cl",
        "published_at": now,
        "title_from_page": True,
        "text": (
            "Suben las tarifas del transporte público este domingo. "
            "La autoridad confirmó la medida y detalló su entrada en vigencia. "
        ) * 6,
    }
    monkeypatch.setattr(main, "score_result", lambda *_args, **_kwargs: 0.23)

    assert not main._is_publishable_realtime_news_source(query, source, page)


def test_current_news_rejects_nested_section_landing_with_fresh_timestamp():
    now = datetime.now(timezone.utc).isoformat()
    url = "https://chile.as.com/noticias/chile"
    source = {
        "title": "Noticias de Chile y el mundo - AS Chile",
        "url": url,
        "source_domain": "chile.as.com",
        "provider": "bing",
        "published_at": now,
        "snippet": "Noticias de Chile y el mundo, actualidad nacional.",
    }
    page = _fetched_page(
        published_at=None,
        url=url,
        title="Noticias de Chile y el mundo - AS Chile",
    )
    page["domain"] = "chile.as.com"
    page["text"] = (
        "Noticias de Chile y el mundo. Revisa actualidad nacional, noticias "
        "de deportes, política, sociedad y economía en esta sección. "
    ) * 10

    assert not _is_publishable_realtime_news_source(
        "últimas noticias de Chile hoy",
        source,
        page,
    )
