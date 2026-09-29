# DEEP33 — Deployment Options

Updated: 2026-09-29

## Current path

The active DEEP33 path is:

Android → Render FastAPI → AI Gateway → Model
Failover → Supabase Edge → AI Gateway/Render → Model

The Android client is wired to three Edge endpoints. The Edge functions route to three distinct Render backend URLs:

- Primary: `deep33-backend` → https://deep33-backend.onrender.com
- Edge failover 1: https://opocgzydeknuchtrqzfa.supabase.co/functions/v1/deep33-proxy
- Edge failover 2: https://opocgzydeknuchtrqzfa.supabase.co/functions/v1/deep33-tertiary

All three Render services are connected to `caggrometal-svg/DEEP33` on GitHub, branch `main`, and are currently configured on Render's free plan.

## Candidate providers

| Provider | Free path | Git integration | DEEP33 fit |
|---|---|---|---|
| Render | Free web service | GitHub | Current backend; Python/FastAPI works without code conversion |
| Koyeb | One Free Instance per organization | GitHub | Good portable FastAPI/Docker fallback; free instance sleeps after inactivity |
| Northflank | Developer Sandbox | GitHub, GitLab, Bitbucket, Azure DevOps and other integrations | Strong option for multi-provider Git connectivity |
| Vercel | Hobby | GitHub, GitLab, Bitbucket, Azure DevOps | Good for the repository's FastAPI adapter; serverless limits must be respected |
| Deno Deploy | Free | GitHub | Strong edge/runtime option, but would require porting the Python backend to Deno/TypeScript |
| Cloudflare Workers | Free | GitHub/GitLab | Strong edge gateway option; resource limits make it a poor direct replacement for the current FastAPI runtime |
| Railway | Free | GitHub | Not the preferred path for DEEP33: current account has no usable DEEP33 project and the free tier is only $1/month after the trial |

## Decision rules

1. Keep the Android API contract stable.
2. Keep Supabase as the memory/search database layer unless a concrete blocker appears.
3. Use GitHub as the canonical source of truth.
4. Prefer providers with automatic deployment from GitHub.
5. Never reintroduce the legacy `deep33.c-33.blitz.cloud` upstream.
6. Do not mark an endpoint ONLINE unless a real network request proves the endpoint and AI path.
7. Any new provider must pass health, readiness, AI inference, web-search, memory, and Android E2E checks before replacing a live node.

## Current blocker state

Railway is not required for the active DEEP33 path.

Render currently reports the three DEEP33 services as non-suspended and connected to GitHub. New commits automatically trigger deployments.

The principal remaining verification gap is GitHub Actions evidence for the current HEAD: the GitHub connector currently reports no workflow run/status objects for the latest commits. This prevents claiming a new green CI/E2E certification solely from repository state.

## Recommended fallback order

Keep the current Render deployment while it remains reachable. For a portable secondary provider, Koyeb is the simplest GitHub-connected Docker path. Northflank is the strongest cross-Git-provider alternative. Vercel/Deno/Cloudflare are better treated as architecture-specific fallbacks rather than drop-in replacements for the current FastAPI service.
