# DEEP33

DEEP33 is a new AI product built independently from IAC33, C-33 and Andrew2.0.

## First gate: real connectivity

The current production chain is:

Android → Internet → DEEP33 Edge Gateway (Supabase) → DEEP33 Backend (Blitz Cloud) → AI Gateway → Model → Android

The backend exposes observable checks instead of a single opaque ONLINE flag.

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
- Public edge gateway: deployed at `https://guqevsjbjyapqjjtutza.supabase.co/functions/v1/deep33-proxy`
- Real Internet + web-search audit: implemented
- Android HTTP client: implemented
- Android instrumented E2E: implemented
- No provider credential is stored in the repository or APK.

Canonical generation contract: `POST /v1/ai/generate` (the legacy `/v1/chat` remains as a compatibility alias).\n\nFASE 3 is closed only after the Android E2E job proves:

Android → Internet → DEEP33 Backend → AI Gateway → Model → Android
