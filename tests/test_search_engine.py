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
    assert results[0]["corroborated"] is True
    assert results[0]["corroboration_count"] >= 1
    assert results[0]["score"] > results[2]["score"]


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
    assert cancelled["ddg"] is True


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
    assert set(result["providers"]) == {"tavily", "bing", "duckduckgo"}
    assert result["verification"]["distinct_providers"] == 3
    assert result["verification"]["distinct_domains"] >= 3
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


def test_search_web_fresh_mode_bypasses_cache(monkeypatch):
    import tools.web_search as web_search_module

    calls = {"count": 0}

    class FakeEngine:
        def __init__(self, *, max_results, max_queries):
            self.max_results = max_results
            self.max_queries = max_queries

        async def search(self, *args, **kwargs):
            calls["count"] += 1
            return {
                "ok": True,
                "engine": "DEEP33 Search Engine",
                "engine_version": "1.3.0",
                "provider_independent": True,
                "results": [{
                    "title": "Live result",
                    "url": f"https://example.com/live-{calls['count']}",
                    "snippet": "fresh",
                }],
            }

    monkeypatch.setattr("backend.search.engine.SearchEngine", FakeEngine)
    web_search_module._SEARCH_CACHE.clear()

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
        }]

    async def fake_bing(query, timeout_seconds, max_results):
        return [{
            "title": "Live Bing",
            "url": "https://bing.example/live",
            "snippet": "latest current news about noticias mundiales",
        }]

    async def fake_ddg(query, timeout_seconds, max_results):
        return [{
            "title": "Live DDG",
            "url": "https://ddg.example/live",
            "snippet": "latest current news about noticias mundiales",
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


def test_realtime_locality_anchor_can_be_found_in_destination_url_path():
    ranked = rank_results(
        "situación actual de la comuna de Las Condes en Chile",
        [
            {
                "title": "Alertas locales publicadas hoy",
                "url": "https://news.example/las-condes/alertas-hoy",
                "snippet": "Actualización local con información municipal.",
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
                }
            ]
        return []

    async def fake_ddg(query, timeout_seconds, max_results):
        return await fake_bing(query, timeout_seconds, max_results)

    monkeypatch.setenv("WEB_SEARCH_BING_ENABLED", "true")
    monkeypatch.setattr("tools.web_search._bing_search", fake_bing)
    monkeypatch.setattr("tools.web_search._duckduckgo_search", fake_ddg)

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
