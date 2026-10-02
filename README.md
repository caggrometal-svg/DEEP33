[![Deploy to Koyeb](https://www.koyeb.com/static/images/deploy/button.svg)](https://app.koyeb.com/deploy?type=git&builder=docker&repository=github.com/caggrometal-svg/DEEP33&branch=main&name=deep33-backend)

# DEEP33

DEEP33 is a new AI product built independently from IAC33, C-33 and Andrew2.0.

## First gate: real connectivity

The current production chain is:

Android → Internet → DEEP33 Edge Gateway (primary) → AI Gateway → Model → Android
Failover 1: Android → Internet → DEEP33 Edge Gateway (secondary route) → AI Gateway → Model → Android

Current backend nodes:
- Edge primary: `https://opocgzydeknuchtrqzfa.supabase.co/functions/v1/deep33-proxy`
- Edge secondary: `https://opocgzydeknuchtrqzfa.supabase.co/functions/v1/deep33-tertiary`

Both Edge routes use the same DEEP33 runtime, durable Postgres-backed idempotency, and the same request/recovery contract. The historical IAC33-labelled Supabase project and legacy hosted backends are not part of the normal DEEP33 client path.

The backend exposes observable checks instead of a single opaque ONLINE flag.

## Free deployment options

DEEP33 is packaged so the FastAPI backend can move between providers without changing the Android contract.

- Render: free web services with automatic deploys from Git; free services have usage and sleep limitations.
- Koyeb: GitHub-driven deployment and one-click deployment are available; the free instance is limited to one per organization.
- Northflank: free Developer Sandbox supports two services and GitHub/GitLab/Bitbucket integrations.
- Deno Deploy: free plan with GitHub deployment support; the current platform is the supported target after the July 20, 2026 retirement of Deno Deploy Classic.
- Cloudflare Workers: free plan is available, but its 128 MB / 10 ms CPU limits make it better suited as an edge gateway than as a direct FastAPI replacement.
- Vercel: GitHub auto-deploy is supported and this repository includes a FastAPI adapter.
## Initial structure

- android/ — Android client
- backend/ — FastAPI backend and AI gateway
- docs/ — architecture and operational documentation
- tests/ — automated tests
- .github/workflows/ — CI

## Connectivity status

- Backend health/readiness: implemented
- Network diagnostics: implemented
- AI status/diagnostics: implemented
- Chat + streaming contracts: implemented
- Public edge gateway: deployed at `https://opocgzydeknuchtrqzfa.supabase.co/functions/v1/deep33-proxy`
- Real Internet + web-search audit: implemented
- Android HTTP client: implemented
- Android instrumented E2E: implemented
- No provider credential is stored in the repository or APK.

Canonical generation contract: `POST /v1/ai/generate` (the legacy `/v1/chat` remains as a compatibility alias).

FASE 3 is closed only after the Android E2E job proves:

Android → Internet → DEEP33 Backend → AI Gateway → Model → Android
