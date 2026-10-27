from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from backend.search.engine import SearchEngine, deduplicate, rank_results, is_realtime_query


def test_search_plan_strips_conversational_web_instructions():
    plan = SearchEngine(max_queries=1).plan(
        "Busca en internet la fecha actual en Chile y responde solo: OK"
    )
    assert plan.queries == ["la fecha actual en Chile"]


def test_search_plan_expands_deep_queries():
    plan = SearchEngine(max_queries=3).plan("investiga DEEP33")
    assert plan.depth == "deep"
    assert len(plan.queries) == 3
    assert plan.queries[0] == "investiga DEEP33"


def test_rank_results_requires_two_topic_anchors_for_two_term_query():
    results = rank_results(
        "Python asyncio official documentation",
        [
            {
                "title": "Python tutorial only",
                "url": "https://example.org/python",
                "snippet": "A guide to Python programming.",
            },
            {
                "title": "asyncio — Asynchronous I/O",
                "url": "https://docs.python.org/3/library/asyncio.html",
                "snippet": "Python asyncio documentation and event loop APIs.",
            },
        ],
    )
    assert [item["url"] for item in results] == [
        "https://docs.python.org/3/library/asyncio.html"
    ]


def test_rank_results_prefers_relevant_and_corroborated_sources():
    results = rank_results(
        "DEEP33 internet",
        [
            {
                "title": "Something unrelated",
                "url": "https://example.net/",
                "snippet": "different topic",
            },
            {
                "title": "DEEP33 internet report",
                "url": "https://source-a.org/report",
                "snippet": "DEEP33 internet source evidence",
            },
            {
                "title": "DEEP33 internet findings",
                "url": "https://source-b.org/findings",
                "snippet": "DEEP33 internet source evidence",
            },
        ],
    )
    assert len(results) == 2  # The unrelated result must be rejected.
    assert results[0]["corroborated"] is True
    assert results[0]["corroboration_count"] >= 1
    assert results[0]["score"] >= results[1]["score"]


def test_deduplicate_normalizes_url_and_guarantees_domain_diversity():
    results = deduplicate(
        [
            {"title": "A1", "url": "https://example.com/a/"},
            {"title": "A2", "url": "https://example.com/b"},
            {"title": "B1", "url": "https://other.example/a"},
            {"title": "Same", "url": "https://other.example/a#fragment"},
        ],
        3,
    )
    assert len(results) == 3
    assert results[0]["url"] == "https://example.com/a"
    assert {item["source_domain"] if "source_domain" in item else item["url"].split("/")[2] for item in results} >= {
        "example.com",
        "other.example",
    }


def test_engine_merges_planned_searches_without_provider_coupling(monkeypatch):
    monkeypatch.setenv("WEB_SEARCH_BING_ENABLED", "false")

    async def fake_tavily(query, api_key, timeout_seconds, max_results):
        return [
            {
                "title": f"Result {query}",
                "url": "https://example.com/a" if "fuente" not in query else "https://example.com/b",
                "snippet": query,
            }
        ]

    monkeypatch.setattr("tools.web_search._tavily_search", fake_tavily)
    result = asyncio.run(
        SearchEngine(max_results=5).search(
            "investiga DEEP33",
            provider="tavily",
            api_key="test-key",
            timeout_seconds=8,
            fallback_ddg=False,
        )
    )
    assert result["ok"] is True
    assert result["engine"] == "DEEP33 Search Engine"
    assert result["engine_version"] == "1.3.0"
    assert result["provider_independent"] is True
    assert result["depth"] == "deep"
    assert len(result["queries"]) == 3
    assert len(result["results"]) == 2
    assert result["provider"] == "tavily"


def test_auto_provider_plan_includes_optional_mojeek_fallback(monkeypatch):
    from backend.search.engine import SearchEngine

    monkeypatch.setenv("WEB_SEARCH_BING_ENABLED", "true")
    monkeypatch.setenv("WEB_SEARCH_MOJEEK_ENABLED", "true")
    engine = SearchEngine()

    assert engine._providers("auto", "", True) == ["bing", "mojeek", "duckduckgo"]
    assert engine._providers("bing", "", True) == ["bing", "mojeek", "duckduckgo"]
    assert engine._providers("mojeek", "", True) == ["mojeek"]


def test_deep_search_stops_after_verified_providers(monkeypatch):
    monkeypatch.setenv("WEB_SEARCH_BING_ENABLED", "true")
    cancelled = {"ddg": False}

    async def fake_tavily(query, api_key, timeout_seconds, max_results):
        return [
            {"title": f"Tavily {i}", "url": f"https://tavily.example/{i}", "snippet": "DEEP33 evidence"}
            for i in range(3)
        ]

    async def fake_bing(query, timeout_seconds, max_results):
        return [
            {"title": f"Bing {i}", "url": f"https://bing.example/{i}", "snippet": "DEEP33 evidence"}
            for i in range(3)
        ]

    async def slow_ddg(query, timeout_seconds, max_results):
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            cancelled["ddg"] = True
            raise

    monkeypatch.setattr("tools.web_search._tavily_search", fake_tavily)
    monkeypatch.setattr("tools.web_search._bing_search", fake_bing)
    monkeypatch.setattr("tools.web_search._duckduckgo_search", slow_ddg)

    result = asyncio.run(
        SearchEngine(max_results=5, max_queries=3).search(
            "investiga DEEP33",
            provider="auto",
            api_key="test-key",
            timeout_seconds=5,
            fallback_ddg=True,
        )
    )

    assert result["ok"] is True
    assert len(result["queries"]) == 3
    assert set(result["providers"]) == {"tavily", "bing"}
    assert cancelled["ddg"] is False


def test_deep_search_uses_multiple_independent_providers(monkeypatch):
    monkeypatch.setenv("WEB_SEARCH_BING_ENABLED", "true")

    async def fake_tavily(query, api_key, timeout_seconds, max_results):
        return [{
            "title": "Shared finding",
            "url": "https://alpha.org/finding",
            "snippet": "DEEP33 internet finding evidence",
        }]

    async def fake_bing(query, timeout_seconds, max_results):
        return [{
            "title": "Shared finding",
            "url": "https://beta.org/finding",
            "snippet": "DEEP33 internet finding evidence",
        }]

    async def fake_ddg(query, timeout_seconds, max_results):
        return [{
            "title": "Different result",
            "url": "https://gamma.net/other",
            "snippet": "unrelated",
        }]

    monkeypatch.setattr("tools.web_search._tavily_search", fake_tavily)
    monkeypatch.setattr("tools.web_search._bing_search", fake_bing)
    monkeypatch.setattr("tools.web_search._duckduckgo_search", fake_ddg)

    result = asyncio.run(
        SearchEngine(max_results=5).search(
            "investiga DEEP33",
            provider="auto",
            api_key="test-key",
            timeout_seconds=8,
            fallback_ddg=True,
        )
    )
    assert result["ok"] is True
    # The unrelated DDG result is excluded from evidence/provider accounting.
    assert set(result["providers"]) == {"tavily", "bing"}
    assert result["verification"]["distinct_providers"] == 2
    assert result["verification"]["distinct_domains"] >= 2
    assert result["verification"]["corroborated_results"] >= 1


def test_standard_search_falls_through_on_weak_primary_results(monkeypatch):
    monkeypatch.setenv("WEB_SEARCH_BING_ENABLED", "true")

    async def fake_bing(query, timeout_seconds, max_results):
        return [{"title": "Weak", "url": "https://a.org/1", "snippet": query}]

    async def fake_ddg(query, timeout_seconds, max_results):
        return [
            {"title": "Strong 1", "url": "https://b.org/1", "snippet": query},
            {"title": "Strong 2", "url": "https://c.org/2", "snippet": query},
            {"title": "Strong 3", "url": "https://d.org/3", "snippet": query},
        ]

    monkeypatch.setattr("tools.web_search._bing_search", fake_bing)
    monkeypatch.setattr("tools.web_search._duckduckgo_search", fake_ddg)

    result = asyncio.run(
        SearchEngine(max_results=5).search(
            "DEEP33 internet",
            provider="bing",
            api_key="",
            timeout_seconds=8,
            fallback_ddg=True,
        )
    )
    assert set(result["providers"]) == {"bing", "duckduckgo"}
    assert len(result["results"]) == 4


def test_standard_search_parallel_provider_short_circuits(monkeypatch):
    monkeypatch.setenv("WEB_SEARCH_BING_ENABLED", "true")
    async def fake_bing(query, timeout_seconds, max_results):
        return [
            {"title": "Enough 1", "url": "https://bing.example/1", "snippet": query},
            {"title": "Enough 2", "url": "https://bing.example/2", "snippet": query},
            {"title": "Enough 3", "url": "https://bing.example/3", "snippet": query},
        ]

    async def fail_ddg(query, timeout_seconds, max_results):
        raise AssertionError("DDG should be cancelled once sufficient results arrive")

    monkeypatch.setattr("tools.web_search._bing_search", fake_bing)
    monkeypatch.setattr("tools.web_search._duckduckgo_search", fail_ddg)

    # The search module defaults to parallel provider acquisition; a sufficient
    # primary result must not force a sequential fallback request.
    result = asyncio.run(
        SearchEngine(max_results=5).search(
            "DEEP33",
            provider="bing",
            api_key="",
            timeout_seconds=8,
            fallback_ddg=True,
        )
    )
    assert result["ok"] is True


def test_fast_mode_returns_after_two_usable_results(monkeypatch):
    monkeypatch.setenv("WEB_SEARCH_BING_ENABLED", "true")
    cancelled = {"value": False}

    async def fast_tavily(query, api_key, timeout_seconds, max_results):
        return [
            {"title": "One", "url": "https://fast2.example/1", "snippet": query},
            {"title": "Two", "url": "https://fast2.example/2", "snippet": query},
        ]

    async def slow_bing(query, timeout_seconds, max_results):
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            cancelled["value"] = True
            raise

    monkeypatch.setattr("tools.web_search._tavily_search", fast_tavily)
    monkeypatch.setattr("tools.web_search._bing_search", slow_bing)

    result = asyncio.run(
        SearchEngine(max_results=5).search(
            "consulta rápida DEEP33",
            provider="auto",
            api_key="test-key",
            timeout_seconds=8,
            fallback_ddg=False,
            fast_mode=True,
        )
    )

    assert result["ok"] is True
    assert len(result["results"]) == 2
    assert cancelled["value"] is True


def test_fast_mode_uses_one_query_and_cancels_slower_provider(monkeypatch):
    monkeypatch.setenv("WEB_SEARCH_BING_ENABLED", "true")
    cancelled = {"value": False}

    async def fast_tavily(query, api_key, timeout_seconds, max_results):
        return [
            {"title": "One", "url": "https://fast.example/1", "snippet": query},
            {"title": "Two", "url": "https://fast.example/2", "snippet": query},
            {"title": "Three", "url": "https://fast.example/3", "snippet": query},
        ]

    async def slow_bing(query, timeout_seconds, max_results):
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            cancelled["value"] = True
            raise

    monkeypatch.setattr("tools.web_search._tavily_search", fast_tavily)
    monkeypatch.setattr("tools.web_search._bing_search", slow_bing)

    result = asyncio.run(
        SearchEngine(max_results=5, max_queries=3).search(
            "investiga DEEP33",
            provider="auto",
            api_key="test-key",
            timeout_seconds=8,
            fallback_ddg=False,
            fast_mode=True,
        )
    )

    assert result["ok"] is True
    assert len(result["queries"]) == 1
    assert result["results"]
    assert cancelled["value"] is True


def test_controversial_queries_expand_against_official_and_independent_versions():
    plan = SearchEngine(max_queries=3).plan("¿La versión oficial contradice la evidencia independiente?")
    assert plan.depth == "deep"
    assert any("versión oficial" in q.lower() for q in plan.queries[1:])
    assert any("evidencia independiente" in q.lower() for q in plan.queries[1:])


def test_two_concept_search_requires_real_topic_overlap_without_overfiltering():
    from backend.search.engine import rank_results

    ranked = rank_results(
        "World Health Organization dengue fact sheet",
        [
            {
                "title": "Dengue",
                "url": "https://www.who.int/news-room/fact-sheets/detail/dengue-and-severe-dengue",
                "snippet": "Dengue is a viral infection transmitted by mosquitoes.",
            },
            {
                "title": "Dengue clinical overview",
                "url": "https://public-health.example/dengue",
                "snippet": "Evidence and prevention guidance for dengue infection.",
            },
            {
                "title": "Unrelated astronomy report",
                "url": "https://science.example/astronomy",
                "snippet": "Stars and galaxies.",
            },
        ],
        realtime=False,
    )

    # WHO is a preferred source, while independent dengue evidence remains
    # eligible for corroboration. Unrelated material must still be rejected.
    assert len(ranked) == 2
    urls = {item["url"] for item in ranked}
    assert any(url.startswith("https://www.who.int/") for url in urls)
    assert "https://public-health.example/dengue" in urls
    assert all("astronomy" not in url for url in urls)


def test_generic_medical_terms_do_not_overfilter_distinct_influenza_topic():
    from backend.search.engine import _topic_tokens, rank_results

    query = "medicina general influenza información oficial"
    assert _topic_tokens(query) == ["influenza"]

    ranked = rank_results(
        query,
        [
            {
                "title": "Influenza (Flu)",
                "url": "https://www.cdc.gov/flu/",
                "snippet": "Official information about influenza prevention and symptoms.",
            },
            {
                "title": "Influenza",
                "url": "https://www.who.int/health-topics/influenza-seasonal",
                "snippet": "Seasonal influenza facts and public health guidance.",
            },
            {
                "title": "Unrelated cardiology article",
                "url": "https://medical.example/cardiology",
                "snippet": "Heart health overview.",
            },
        ],
        realtime=False,
    )

    assert len(ranked) == 2
    assert all("cardiology" not in item["url"] for item in ranked)


def test_realtime_economics_ranking_normalizes_cross_language_and_inflections():
    from backend.search.engine import rank_results

    ranked = rank_results(
        "noticias económicas Chile hoy",
        [
            {
                "title": "Economía y mercados: últimas noticias",
                "url": "https://economia.example/chile",
                "snippet": "Finanzas, mercados y actividad económica en Chile.",
                "published_at": datetime.now(timezone.utc).isoformat(),
            },
            {
                "title": "Chile economy and financial markets",
                "url": "https://markets.example/chile",
                "snippet": "Current economic and market developments.",
                "published_at": datetime.now(timezone.utc).isoformat(),
            },
            {
                "title": "Football results",
                "url": "https://sports.example/results",
                "snippet": "International match scores.",
            },
        ],
        realtime=True,
    )

    assert len(ranked) == 2
    assert {item["source_domain"] for item in ranked} == {
        "economia.example",
        "markets.example",
    }


def test_realtime_weather_ranking_accepts_spanish_english_semantic_equivalence():
    from backend.search.engine import rank_results

    ranked = rank_results(
        "clima actual Santiago Chile",
        [
            {
                "title": "Santiago Weather Forecast",
                "url": "https://weather.example/santiago",
                "snippet": "Current forecast and temperature for Santiago",
            },
            {
                "title": "Pronóstico del tiempo en Santiago",
                "url": "https://meteo.example/santiago",
                "snippet": "Temperatura y condiciones meteorológicas en Santiago",
            },
            {
                "title": "Unrelated market report",
                "url": "https://finance.example/report",
                "snippet": "Stocks and bonds",
            },
        ],
        realtime=True,
    )

    assert len(ranked) == 2
    assert {item["source_domain"] for item in ranked} == {
        "weather.example",
        "meteo.example",
    }


def test_search_web_fresh_mode_bypasses_cache(monkeypatch):
    import tools.web_search as web_search_module
    import backend.search.resilient as resilient_module

    calls = {"count": 0}

    class FakeOrchestrator:
        async def search(self, query, *, fresh=False):
            assert fresh is True
            calls["count"] += 1
            return {
                "ok": True,
                "engine": "DEEP33 Search Engine",
                "engine_version": "1.4.0",
                "provider_independent": True,
                "results": [{
                    "title": "Live result",
                    "url": f"https://example.com/live-{calls['count']}",
                    "snippet": "fresh",
                }],
            }

    fake = FakeOrchestrator()
    monkeypatch.setattr(resilient_module, "get_web_orchestrator", lambda max_results=5: fake)

    asyncio.run(web_search_module.search_web("DEEP33 ahora", provider="auto", fresh=True))
    asyncio.run(web_search_module.search_web("DEEP33 ahora", provider="auto", fresh=True))

    assert calls["count"] == 2


def test_realtime_policy_detects_local_current_situation_as_news_intent():
    query = "situación actual de la comuna de Las Condes en Chile"
    assert is_realtime_query(query)
    plan = SearchEngine(max_queries=3).plan(query)
    assert plan.depth == "realtime"
    assert any("últimas noticias de hoy" in q.lower() for q in plan.queries[1:])


def test_realtime_rank_drops_unrelated_current_results():
    results = [
        {
            "title": "Las Condes registra emergencia por lluvias",
            "url": "https://news.example/las-condes",
            "snippet": "Actualización de la situación en Las Condes.",
                "published_at": datetime.now(timezone.utc).isoformat(),
        },
        {
            "title": "Actualización de Windows",
            "url": "https://tech.example/windows",
            "snippet": "Novedades y cambios actuales del sistema operativo.",
        },
    ]
    ranked = rank_results(
        "situación actual de la comuna de Las Condes en Chile",
        results,
        realtime=True,
    )
    assert len(ranked) == 1
    assert ranked[0]["url"] == "https://news.example/las-condes"


def test_realtime_rank_rejects_country_only_match_for_named_locality():
    query = "situación actual de la comuna de Las Condes en Chile"
    results = [
        {
            "title": "Hospedaje en Copiapó, Chile",
            "url": "https://travel.example/copiapo",
            "snippet": "Hospedaje cerca de la universidad en Copiapó, Chile.",
        },
        {
            "title": "Aluvión afecta San Carlos de Apoquindo en Las Condes",
            "url": "https://news.example/las-condes-live",
            "snippet": "Emergencia local en Las Condes tras las lluvias.",
                "published_at": datetime.now(timezone.utc).isoformat(),
        },
    ]
    ranked = rank_results(query, results, realtime=True)
    assert [item["url"] for item in ranked] == ["https://news.example/las-condes-live"]


def test_realtime_broad_country_news_does_not_require_a_country_anchor():
    results = [
        {
            "title": "Noticias de Chile hoy: autoridades anuncian nuevas medidas",
            "url": "https://news.example/chile-live",
            "snippet": "Actualización de última hora publicada hoy.",
                "published_at": datetime.now(timezone.utc).isoformat(),
        },
        {
            "title": "Partido Republicano de Chile",
            "url": "https://en.wikipedia.org/?curid=61114536",
            "snippet": "Información enciclopédica histórica sobre un partido.",
        },
        {
            "title": "Actualización de Windows",
            "url": "https://tech.example/windows",
            "snippet": "Novedades del sistema operativo.",
        },
    ]
    ranked = rank_results("noticias recientes en Chile", results, realtime=True)
    assert ranked
    assert ranked[0]["url"] == "https://news.example/chile-live"
    assert all("wikipedia.org" not in item["url"] for item in ranked)
    assert "https://tech.example/windows" not in [item["url"] for item in ranked]


def test_realtime_world_news_does_not_overfit_to_literal_world_synonyms():
    results = [
        {
            "title": "Partido Republicano de Chile",
            "url": "https://en.wikipedia.org/?curid=61114536",
            "snippet": "Información enciclopédica.",
        },
        {
            "title": "International leaders meet for a new agreement",
            "url": "https://news.example/world",
            "snippet": "Últimas noticias internacionales publicadas hoy.",
                "published_at": datetime.now(timezone.utc).isoformat(),
        },
    ]
    ranked = rank_results("noticias mundiales", results, realtime=True)
    assert [item["url"] for item in ranked] == ["https://news.example/world"]


def test_realtime_policy_catches_news_weather_and_politics():
    assert is_realtime_query("Noticias mundiales")
    assert is_realtime_query("¿Cómo está el clima hoy?")
    assert is_realtime_query("¿Quién es el presidente de Chile?")
    assert not is_realtime_query("¿Quién fue el presidente de Chile en 1990?")


def test_realtime_search_plan_adds_current_date_variants():
    plan = SearchEngine(max_queries=3).plan("Noticias mundiales")
    assert plan.depth == "realtime"
    assert plan.queries[0] == "Noticias mundiales"
    assert len(plan.queries) == 3
    today = datetime.now(timezone.utc).date().isoformat()
    assert all(today in query for query in plan.queries[1:])


def test_realtime_weather_search_targets_local_conditions_and_official_source():
    plan = SearchEngine(max_queries=3).plan(
        "¿Cómo está el clima en Recoleta, Santiago?"
    )
    assert plan.depth == "realtime"
    assert len(plan.queries) == 3
    assert "Recoleta, Santiago" in plan.queries[0]
    today = datetime.now(timezone.utc).date().isoformat()
    assert all(today in query for query in plan.queries[1:])
    assert any("temperatura" in query.lower() and "humedad" in query.lower() for query in plan.queries[1:])
    assert any("site:meteochile.gob.cl" in query for query in plan.queries[1:])


def test_realtime_search_uses_multiple_providers(monkeypatch):
    monkeypatch.setenv("WEB_SEARCH_BING_ENABLED", "true")

    async def fake_tavily(query, api_key, timeout_seconds, max_results):
        return [{
            "title": "Live Tavily",
            "url": "https://tavily.example/live",
            "snippet": "latest current news about noticias mundiales",
                "published_at": datetime.now(timezone.utc).isoformat(),
        }]

    async def fake_bing(query, timeout_seconds, max_results):
        return [{
            "title": "Live Bing",
            "url": "https://bing.example/live",
            "snippet": "latest current news about noticias mundiales",
                "published_at": datetime.now(timezone.utc).isoformat(),
        }]

    async def fake_ddg(query, timeout_seconds, max_results):
        return [{
            "title": "Live DDG",
            "url": "https://ddg.example/live",
            "snippet": "latest current news about noticias mundiales",
                "published_at": datetime.now(timezone.utc).isoformat(),
        }]

    monkeypatch.setattr("tools.web_search._tavily_search", fake_tavily)
    monkeypatch.setattr("tools.web_search._bing_search", fake_bing)
    monkeypatch.setattr("tools.web_search._duckduckgo_search", fake_ddg)

    result = asyncio.run(
        SearchEngine(max_results=5).search(
            "Noticias mundiales",
            provider="auto",
            api_key="test-key",
            timeout_seconds=8,
            fallback_ddg=True,
        )
    )

    assert result["ok"] is True
    assert result["realtime"] is True
    assert result["depth"] == "realtime"
    assert len(result["queries"]) == 3
    assert len(result["providers"]) == 2
    assert result["verification"]["distinct_providers"] == 2


def test_realtime_search_does_not_wait_for_a_dead_secondary_provider(monkeypatch):
    import backend.search.engine as module

    monkeypatch.setenv("WEB_SEARCH_BING_ENABLED", "true")
    monkeypatch.setattr(module, "REALTIME_CORROBORATION_WINDOW_SECONDS", 0.03)

    cancelled = {"ddg": False}

    async def fast_bing(query, timeout_seconds, max_results):
        return [{
            "title": "Las Condes: alertas locales publicadas hoy",
            "url": "https://news.example/las-condes/alertas-hoy",
            "snippet": "Actualización local con información municipal.",
                "published_at": datetime.now(timezone.utc).isoformat(),
        }]

    async def hanging_ddg(query, timeout_seconds, max_results):
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            cancelled["ddg"] = True
            raise

    monkeypatch.setattr("tools.web_search._bing_search", fast_bing)
    monkeypatch.setattr("tools.web_search._duckduckgo_search", hanging_ddg)

    result = asyncio.run(
        SearchEngine(max_results=5, max_queries=1).search(
            "situación actual de la comuna de Las Condes en Chile",
            provider="auto",
            api_key="",
            timeout_seconds=3,
            fallback_ddg=True,
        )
    )

    assert result["ok"] is True
    assert result["results"][0]["url"] == "https://news.example/las-condes/alertas-hoy"
    assert "bing" in result["providers"]
    assert cancelled["ddg"] is False


def test_realtime_locality_anchor_can_be_found_in_destination_url_path():
    ranked = rank_results(
        "situación actual de la comuna de Las Condes en Chile",
        [
            {
                "title": "Alertas locales publicadas hoy",
                "url": "https://news.example/las-condes/alertas-hoy",
                "snippet": "Actualización local con información municipal.",
                "published_at": datetime.now(timezone.utc).isoformat(),
            },
            {
                "title": "Actualización del sistema operativo",
                "url": "https://tech.example/windows",
                "snippet": "Noticias de tecnología publicadas hoy.",
            },
        ],
        realtime=True,
    )
    assert [item["url"] for item in ranked] == [
        "https://news.example/las-condes/alertas-hoy"
    ]


def test_realtime_locality_rescue_keeps_the_place_name(monkeypatch):
    from datetime import datetime, timezone

    async def fake_bing(query, timeout_seconds, max_results):
        if query.startswith("Las Condes Chile noticias hoy "):
            return [
                {
                    "title": "Alertas locales publicadas hoy",
                    "url": "https://news.example/las-condes/alertas-hoy",
                    "snippet": "Actualización local con información municipal.",
                "published_at": datetime.now(timezone.utc).isoformat(),
                }
            ]
        return []

    async def fake_ddg(query, timeout_seconds, max_results):
        return await fake_bing(query, timeout_seconds, max_results)

    async def fake_google_news(query, timeout_seconds, max_results):
        return []

    monkeypatch.setenv("WEB_SEARCH_BING_ENABLED", "true")
    monkeypatch.setattr("tools.web_search._bing_search", fake_bing)
    monkeypatch.setattr("tools.web_search._duckduckgo_search", fake_ddg)
    monkeypatch.setattr("tools.web_search._google_news_search", fake_google_news)

    result = asyncio.run(
        SearchEngine(max_results=5, max_queries=3).search(
            "situación actual de la comuna de Las Condes en Chile",
            provider="auto",
            api_key="",
            timeout_seconds=5,
            fallback_ddg=True,
        )
    )
    expected_rescue = (
        "Las Condes Chile noticias hoy "
        + datetime.now(timezone.utc).date().isoformat()
    )
    assert result["ok"] is True
    assert expected_rescue in result["queries_executed"]
    assert result["results"][0]["url"] == "https://news.example/las-condes/alertas-hoy"


def test_realtime_anchor_tokens_ignore_numeric_date_suffixes():
    from backend.search.engine import _realtime_anchor_tokens

    anchors = _realtime_anchor_tokens("Las Condes Chile noticias hoy 2026-10-09")
    assert "condes" in anchors
    assert "2026" not in anchors
    assert "10" not in anchors


def test_realtime_news_anchor_ignores_prompt_boilerplate_and_calendar_date():
    from backend.search.engine import _realtime_anchor_tokens

    query = (
        "Busca una noticia publicada hoy, 10 de octubre de 2026, en Chile. "
        "Usa una fuente real y actualizada. Indica el titular, la fecha de publicación, "
        "un resumen breve y el enlace original. No inventes información."
    )
    assert _realtime_anchor_tokens(query) == {"chile"}
    ranked = rank_results(
        query,
        [{
            "title": "Chile anuncia nuevas medidas este sábado",
            "url": "https://news.example/chile/actualidad",
            "snippet": "Autoridades informaron hoy de nuevas medidas en Chile.",
                "published_at": datetime.now(timezone.utc).isoformat(),
        }],
        realtime=True,
    )
    assert len(ranked) == 1


def test_realtime_search_stops_after_a_relevant_provider_result(monkeypatch):
    queries = []

    async def fake_run_query(self, providers, planned_query, **kwargs):
        queries.append(planned_query)
        return (
            [("google_news", [
                {
                    "title": "Chile anuncia nuevas medidas",
                    "url": "https://news.example/chile-medidas",
                    "snippet": "Noticia actual publicada hoy en Chile.",
                "published_at": datetime.now(timezone.utc).isoformat(),
                },
                {
                    "title": "Gobierno de Chile informa nuevos cambios",
                    "url": "https://report.example/chile-cambios",
                    "snippet": "Actualización reciente de una fuente informativa.",
                "published_at": datetime.now(timezone.utc).isoformat(),
                },
            ])],
            [],
            ["google_news"],
        )

    monkeypatch.setattr(SearchEngine, "_run_query", fake_run_query)
    result = asyncio.run(
        SearchEngine(max_results=5, max_queries=3).search(
            "noticias recientes en Chile",
            provider="auto",
            api_key="",
            timeout_seconds=3,
            fallback_ddg=True,
        )
    )
    assert result["ok"] is True
    assert len(queries) == 1
    assert result["queries_executed"] == [queries[0]]


def test_rank_results_normalizes_ai_and_technology_synonyms():
    query = "latest artificial intelligence research and technology news today"
    ranked = rank_results(
        query,
        [
            {
                "title": "AI tech research news today",
                "url": "https://example.org/ai-tech-research",
                "snippet": "Research and analysis of AI technology.",
                "published_at": datetime.now(timezone.utc).isoformat(),
            }
        ],
        realtime=True,
    )
    assert len(ranked) == 1
    assert ranked[0]["url"] == "https://example.org/ai-tech-research"


def test_named_weather_fallback_recovers_after_search_providers_return_empty(monkeypatch):
    from tools.web_search import search_web
    from backend.search.resilient import WebOrchestrator

    async def empty_search(self, query: str, *, fresh: bool = False):
        return {
            "ok": False,
            "status": "NO_RESULTS",
            "diagnostic_code": "NO_RESULTS",
            "retrieved_at": "2026-10-13T00:00:00+00:00",
            "results": [],
            "sources": [],
            "providers": [],
            "providers_attempted": ["bing", "duckduckgo"],
            "verification": {"level": "none", "distinct_domains": 0, "distinct_providers": 0, "corroborated_results": 0},
            "errors": ["bing:NO_RESULTS", "duckduckgo:NO_RESULTS"],
        }

    async def weather(query: str, *, timeout_seconds: float):
        assert "Santiago" in query
        return {
            "location_name": "Santiago",
            "country": "Chile",
            "temperature_c": 21.4,
            "condition": "despejado",
            "relative_humidity_pct": 40,
            "wind_speed_kmh": 9.0,
            "time": "2026-10-13T01:00",
            "timezone": "America/Santiago",
            "source_url": "https://api.open-meteo.com/v1/forecast?latitude=-33.45&longitude=-70.66&current=temperature_2m",
        }

    monkeypatch.setattr(WebOrchestrator, "search", empty_search)
    monkeypatch.setattr("backend.weather.fetch_named_location_weather", weather)
    result = asyncio.run(search_web("Clima actual en Santiago de Chile", fresh=True))

    assert result["ok"] is True
    assert result["provider"] == "open_meteo"
    assert "open_meteo" in result["providers_attempted"]
    assert len(result["results"]) == 1
    assert "Santiago, Chile" in result["results"][0]["title"]
    assert result["retrieved_at"]


def test_standard_orchestrator_does_not_wait_for_slow_second_provider_after_three_domains():
    from backend.search.resilient import WebOrchestrator

    async def scenario():
        orchestrator = WebOrchestrator(
            max_results=5,
            provider_total_seconds=1.5,
            global_budget_seconds=2.0,
            fallback_delay_seconds=0.0,
        )
        cancelled = {"count": 0}

        async def fast(query: str, timeout: float, limit: int):
            return [
                {"title": "Python asyncio A", "url": "https://a.example/python-asyncio", "snippet": "Python asyncio documentation"},
                {"title": "Python asyncio B", "url": "https://b.example/python-asyncio", "snippet": "Python asyncio documentation"},
                {"title": "Python asyncio C", "url": "https://c.example/python-asyncio", "snippet": "Python asyncio documentation"},
            ]

        async def slow(query: str, timeout: float, limit: int):
            try:
                await asyncio.sleep(1.5)
                return []
            except asyncio.CancelledError:
                cancelled["count"] += 1
                raise

        orchestrator.providers = {"bing": fast, "google": slow, "duckduckgo": slow}
        orchestrator.circuits = {name: orchestrator.circuits[name] for name in orchestrator.providers}
        started = asyncio.get_running_loop().time()
        result = await orchestrator.search("Python asyncio official documentation", fresh=True)
        elapsed = asyncio.get_running_loop().time() - started

        assert result["ok"] is True
        assert len({item["source_domain"] for item in result["results"]}) >= 3
        assert elapsed < 0.8
        assert cancelled["count"] >= 1

    asyncio.run(scenario())

def test_realtime_search_respects_explicit_nasa_authority_with_one_topic_anchor():
    results = [
        {
            "title": "NASA's Artemis missions",
            "url": "https://www.nasa.gov/humans-in-space/artemis/",
            "snippet": "NASA describes the Artemis campaign and its lunar mission objectives.",
        },
        {
            "title": "NASA agency overview",
            "url": "https://www.nasa.gov/about/",
            "snippet": "Information about NASA leadership and agency administration.",
        },
    ]
    ranked = rank_results("nasa artemis mission official", results, realtime=True)

    assert any("artemis" in item["url"].casefold() for item in ranked)
    assert all("/about/" not in item["url"] for item in ranked)

def test_academic_ai_search_accepts_machine_learning_terminology_but_rejects_unrelated_papers():
    results = [
        {
            "title": "Machine Learning Research at the University",
            "url": "https://arxiv.org/abs/2601.12345",
            "snippet": "A university research paper about machine learning models and neural networks.",
        },
        {
            "title": "University research paper about Roman architecture",
            "url": "https://example.edu/roman-architecture",
            "snippet": "This academic study examines Roman architecture and historical buildings.",
        },
    ]
    ranked = rank_results(
        "universidad investigación inteligencia artificial paper",
        results,
        realtime=False,
    )

    assert any("arxiv.org" in item["url"] for item in ranked)
    assert all("roman-architecture" not in item["url"] for item in ranked)



def test_chile_public_info_uses_live_fetchable_portal_candidates_when_indexes_collapse() -> None:
    from backend.search.resilient import WebOrchestrator

    async def scenario() -> None:
        orchestrator = WebOrchestrator(
            max_results=5,
            provider_total_seconds=0.12,
            global_budget_seconds=0.8,
            fallback_delay_seconds=0.01,
        )

        async def sparse_bing(query: str, timeout: float, limit: int) -> list[dict]:
            if "chile" in query.casefold() and "site:" not in query.casefold():
                return [{
                    "title": "Gobierno de Chile - Gob.cl",
                    "url": "https://www.gob.cl/",
                    "snippet": "Portal oficial del Gobierno de Chile.",
                    "source_domain": "www.gob.cl",
                }]
            return []

        async def empty_provider(query: str, timeout: float, limit: int) -> list[dict]:
            return []

        orchestrator.providers = {
            "bing": sparse_bing,
            "google": empty_provider,
            "google_news": empty_provider,
            "cl0q": empty_provider,
            "mojeek": empty_provider,
            "duckduckgo": empty_provider,
        }
        orchestrator.fallback_providers = {"yahoo": empty_provider}

        result = await orchestrator.search(
            "sitio gubernamental Chile información pública",
            fresh=True,
        )
        urls = {str(item.get("url") or "") for item in result.get("results", [])}
        domains = {
            str(item.get("source_domain") or "").casefold().removeprefix("www.")
            for item in result.get("results", [])
        }
        expected = {
            "https://www.chileatiende.gob.cl/",
            "https://datos.gob.cl/",
            "https://www.bcn.cl/leychile",
            "https://www.consejotransparencia.cl/",
        }
        assert result["ok"] is True, result
        assert len(domains - {""}) >= 3, result
        assert len(urls & expected) >= 3, result
        assert "https://www.gob.cl/" not in urls

    asyncio.run(scenario())



def test_chile_commercial_site_fallback_returns_official_navigation_candidates_when_indexes_collapse() -> None:
    from backend.search.resilient import WebOrchestrator

    async def scenario() -> None:
        orchestrator = WebOrchestrator(
            max_results=5,
            provider_total_seconds=0.12,
            global_budget_seconds=0.8,
            fallback_delay_seconds=0.01,
        )

        async def directory_only(query: str, timeout: float, limit: int) -> list[dict]:
            return [{
                "title": "Tecnología en Chile — Directorio de empresas",
                "url": "https://www.encuentraempresas.cl/categoria-tecnologia.html",
                "snippet": "Directorio comercial de proveedores de tecnología en Chile.",
                "source_domain": "encuentraempresas.cl",
            }]

        orchestrator.providers = {
            "bing": directory_only,
            "google": directory_only,
            "google_news": directory_only,
            "cl0q": directory_only,
            "mojeek": directory_only,
            "duckduckgo": directory_only,
        }
        orchestrator.fallback_providers = {"yahoo": directory_only}

        result = await orchestrator.search("sitio comercial tecnología Chile", fresh=True)
        urls = {str(item.get("url") or "") for item in result.get("results", [])}
        domains = {
            str(item.get("source_domain") or "").casefold().removeprefix("www.")
            for item in result.get("results", [])
        }
        expected = {
            "https://www.sonda.com/",
            "https://www.entel.cl/empresas/",
            "https://www.wom.cl/empresas/",
            "https://ww2.movistar.cl/empresas/",
        }
        assert result["ok"] is True, result
        assert len(domains - {""}) >= 3, result
        assert len(urls & expected) >= 3, result
        assert "https://www.encuentraempresas.cl/categoria-tecnologia.html" not in urls

    asyncio.run(scenario())


def test_realtime_ranking_rejects_lexical_translation_noise():
    ranked = rank_results(
        "noticias Chile hoy cambios recientes",
        [
            {
                "title": "Chile - English translation and dictionary",
                "url": "https://www.spanishdict.com/translate/chile",
                "snippet": "Spanish-English translation, conjugation and grammar.",
                "published_at": datetime.now(timezone.utc).isoformat(),
            },
            {
                "title": "Noticias de Chile hoy: cambios recientes",
                "url": "https://news.example.cl/chile/cambios-recientes",
                "snippet": "Actualidad y noticias de Chile con cambios recientes confirmados.",
                "published_at": datetime.now(timezone.utc).isoformat(),
            },
        ],
        realtime=True,
    )
    assert [item["source_domain"] for item in ranked] == ["news.example.cl"]


def test_realtime_news_rejects_explicitly_stale_dated_result():
    old = datetime(2020, 1, 1, tzinfo=timezone.utc).isoformat()
    fresh = datetime.now(timezone.utc).isoformat()
    ranked = rank_results(
        "últimas noticias de Chile hoy",
        [
            {
                "title": "Últimas noticias de Chile hoy",
                "url": "https://stale.example.cl/chile/noticias",
                "snippet": "Noticias y actualidad de Chile.",
                "published_at": old,
            },
            {
                "title": "Noticias de Chile hoy",
                "url": "https://fresh.example.cl/chile/noticias",
                "snippet": "Últimas noticias y actualidad de Chile.",
                "published_at": fresh,
            },
        ],
        realtime=True,
    )
    assert [item["source_domain"] for item in ranked] == ["fresh.example.cl"]


def test_realtime_orchestrator_never_reads_or_writes_cache():
    from backend.search.resilient import WebOrchestrator

    calls = {"count": 0}

    async def provider(query, timeout, limit):
        calls["count"] += 1
        return [
            {
                "title": "DEEP33 noticias actuales",
                "url": f"https://news.example/item-{calls['count']}",
                "snippet": "DEEP33 noticias actualidad hoy",
                "published_at": datetime.now(timezone.utc).isoformat(),
            }
        ]

    async def exercise():
        orchestrator = WebOrchestrator(
            max_results=5,
            provider_total_seconds=0.1,
            global_budget_seconds=0.3,
            fallback_delay_seconds=0,
            cache_ttl_seconds=120,
        )
        orchestrator.providers = {"bing": provider}
        orchestrator.fallback_providers = {}
        orchestrator.circuits = {"bing": orchestrator.circuits["bing"]}

        first = await orchestrator.search("DEEP33 noticias hoy", fresh=False)
        second = await orchestrator.search("DEEP33 noticias hoy", fresh=False)
        return first, second, orchestrator

    first, second, orchestrator = asyncio.run(exercise())
    assert first["ok"] is True
    assert second["ok"] is True
    # Each top-level realtime search executes two planned query variants. Both
    # variants must execute again on the second call because realtime is cache-free.
    assert calls["count"] == 4
    assert first["cache_hit"] is False
    assert second["cache_hit"] is False
    assert orchestrator.cache.get(orchestrator._cache_key("DEEP33 noticias hoy")) is None


def test_realtime_general_web_queries_do_not_inherit_newsroom_constraints():
    from backend.search.engine import is_realtime_news_query, is_realtime_query

    general_realtime = (
        "¿Cuál es la situación actual de Python 3.14?",
        "¿Cuál es la última versión estable de FastAPI?",
        "Revisa las release notes recientes de Android.",
        "¿Cuál es el estado actual del servicio de GitHub?",
        "Busca el security advisory vigente de este paquete.",
    )
    for query in general_realtime:
        assert is_realtime_query(query) is True
        assert is_realtime_news_query(query) is False

    assert is_realtime_news_query("¿Cuáles son las noticias más importantes de Chile hoy?") is True
