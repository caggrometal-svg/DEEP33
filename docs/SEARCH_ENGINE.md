# DEEP33 Search Engine

DEEP33 Search Engine is the provider-agnostic orchestration layer used by the server-side web Tool Loop.

Pipeline:

`query -> plan -> providers -> normalize -> deduplicate -> rank -> verify -> sources`

Latency/quality behavior:

- The first query races independent providers and stops as soon as the minimum verification set is satisfied.
- Deep/realtime query variants are expanded only when the first provider set is insufficient; the default query fan-out is capped at two.
- Search provider retries default to zero because provider failover is faster than waiting through duplicate attempts.
- Page retrieval is optional when search snippets already provide sufficient evidence; explicit research fetches at most two pages in parallel.

Hardened behavior:

- Deep/research queries execute across all configured providers instead of accepting the first successful provider.
- Standard queries fall through to the next provider when the primary returns fewer than the minimum useful results.
- Provider calls have bounded retries and timeouts.
- Results are normalized and SSRF-validated before they enter the engine.
- URLs and titles are deduplicated; selection favors distinct domains and caps repeated domains.
- Ranking combines query relevance, source quality, freshness and cross-domain corroboration.
- Each result exposes provider, search query, domain and corroboration metadata.
- Verification reports whether the response is single-source, multi-source or corroborated.
- Provider failures are recorded without hiding successful providers.

Providers:

- Tavily when configured.
- Bing public search with HTML parsing plus RSS rescue.
- DuckDuckGo fallback.
- The engine contract is independent of any one provider; providers are adapters behind the same interface.
- Engine version: 1.3.0.

Production contract:

- `GET /v1/web/status` exposes engine/provider configuration.
- `GET /v1/web/search?q=...` runs the Search Engine.
- `POST /v1/ai/generate` and `POST /v1/chat/stream` can invoke it through the server-side Tool Loop.
- Render release acceptance requires all three production services to report the same Git SHA as GitHub `main`.
