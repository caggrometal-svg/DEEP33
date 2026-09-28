# DEEP33 Search Engine

DEEP33 Search Engine is the orchestration layer used by the server-side web Tool Loop.

Pipeline:

`query -> plan -> providers -> normalize -> deduplicate -> rank -> sources`

Capabilities:

- Standard and research-oriented query planning.
- Up to three related searches for research queries.
- Tavily when configured.
- Bing public search as an additional provider/fallback.
- DuckDuckGo fallback.
- Result normalization to title, URL and snippet.
- URL and title deduplication.
- Ranking by lexical relevance, domain quality and freshness.
- Existing SSRF-protected web fetch remains the page-reading layer.

Production contract:

- `GET /v1/web/status` exposes engine/provider configuration.
- `GET /v1/web/search?q=...` runs the Search Engine.
- `POST /v1/ai/generate` can invoke it through the server-side Tool Loop.
- Render release acceptance requires all three production services to report the same Git SHA as GitHub `main`.
