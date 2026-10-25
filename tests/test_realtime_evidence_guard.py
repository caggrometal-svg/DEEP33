from datetime import datetime, timedelta, timezone

from backend.search.engine import (
    _is_lexical_reference_result,
    _is_recently_published,
    is_realtime_news_query,
    rank_results,
)


def _published_ago(**kwargs) -> str:
    return (datetime.now(timezone.utc) - timedelta(**kwargs)).isoformat()


def test_current_news_queries_are_explicitly_recognized():
    assert is_realtime_news_query("Busca noticias recientes de Chile")
    assert is_realtime_news_query("¿Qué cambios recientes hubo en DEEP33?")
    assert not is_realtime_news_query("¿Cómo funciona un diccionario bilingüe?")


def test_dictionary_and_translation_sites_are_not_news_sources():
    assert _is_lexical_reference_result({
        "url": "https://www.bab.la/dictionary/english-spanish/current",
        "title": "Current - translation",
    })
    assert _is_lexical_reference_result({
        "url": "https://www.spanishdict.com/translate/current",
        "title": "Current | SpanishDict",
    })
    assert _is_lexical_reference_result({
        "url": "https://dictionary.cambridge.org/dictionary/english/current",
        "title": "Current definition",
    })
    assert not _is_lexical_reference_result({
        "url": "https://news.example.cl/chile/current-events",
        "title": "Noticias actuales de Chile",
    })


def test_recent_news_requires_valid_fresh_publication_timestamp():
    query = "noticias recientes en Chile"
    assert _is_recently_published(
        {"published_at": _published_ago(hours=2)}, query
    )
    assert not _is_recently_published({}, query)
    assert not _is_recently_published(
        {"published_at": _published_ago(days=8)}, query
    )
    assert not _is_recently_published(
        {"published_at": "not-a-date"}, query
    )
    assert not _is_recently_published(
        {"published_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()},
        query,
    )


def test_news_ranking_rejects_dictionary_junk_stale_and_undated_results():
    query = "noticias recientes en Chile"
    ranked = rank_results(
        query,
        [
            {
                "title": "Noticias recientes de Chile: últimas medidas anunciadas",
                "url": "https://news.example.cl/chile/medidas",
                "source_domain": "news.example.cl",
                "snippet": "Actualización reciente publicada en Chile con las medidas anunciadas hoy.",
                "published_at": _published_ago(hours=2),
            },
            {
                "title": "Current - Spanish translation",
                "url": "https://www.bab.la/dictionary/english-spanish/current",
                "source_domain": "bab.la",
                "snippet": "Noticias recientes actuales y definición de la palabra current.",
                "published_at": _published_ago(hours=1),
            },
            {
                "title": "Noticias de Chile",
                "url": "https://stale.example.cl/chile/archivo",
                "source_domain": "stale.example.cl",
                "snippet": "Noticias recientes sobre Chile y medidas anunciadas.",
                "published_at": _published_ago(days=10),
            },
            {
                "title": "Noticias recientes en Chile",
                "url": "https://undated.example.cl/chile/noticias",
                "source_domain": "undated.example.cl",
                "snippet": "Actualización de noticias sobre Chile y medidas anunciadas.",
            },
        ],
        realtime=True,
    )
    assert [item["url"] for item in ranked] == [
        "https://news.example.cl/chile/medidas"
    ]
