# DEEP33 free inference fallbacks

This change adds optional Cloudflare Workers AI and Groq-compatible chat-completion providers to the existing Supabase Edge routes. It does not change Android endpoint URLs or the current primary-provider order.

## Safe defaults

- Disabled unless `DEEP33_ENABLE_FREE_INFERENCE_FALLBACKS=true`.
- Credentials are read only from Supabase Edge Function environment secrets. Never commit keys to GitHub, place them in the APK, or send them in chat.
- The current configured AI provider remains first. Optional free providers are tried before the Render fallback.
- Cloudflare Workers AI is asked to reject busy capacity instead of queueing (`options.rejectIfBusy=true`).
- Optional providers have a 4-second first-token deadline and a 7-second deadline for non-streaming requests. Existing streaming responses are not switched to another provider after a chunk has already reached the user.
- If a credential is absent or the Cloudflare account ID is invalid, that provider is not added to the route.
- Provider secrets are not printed to logs.

## Configure in Supabase

In the Supabase project that hosts `deep33-proxy` and `deep33-tertiary`, add only the providers you have configured:

| Secret / environment variable | Purpose |
|---|---|
| `DEEP33_ENABLE_FREE_INFERENCE_FALLBACKS` | Explicit opt-in; set to `true` only after reviewing provider billing controls |
| `DEEP33_CLOUDFLARE_ACCOUNT_ID` | Cloudflare account ID (32 hexadecimal characters) |
| `DEEP33_CLOUDFLARE_API_TOKEN` | Server-side token allowed to run Workers AI |
| `DEEP33_CLOUDFLARE_MODEL` | Optional; defaults to `@cf/meta/llama-3.1-8b-instruct-fp8` |
| `DEEP33_GROQ_API_KEY` | Server-side Groq API key |
| `DEEP33_GROQ_MODEL` | Optional; defaults to `openai/gpt-oss-20b` |

Use the Supabase Dashboard's Edge Function secrets/environment settings or the Supabase CLI's secrets command for the correct project reference. Deploy both functions after setting secrets. Do not put any of the secret values in `render.yaml`, GitHub files, or Android build configuration.

## Zero-cost controls

Free-tier quotas are provider-account quotas, not per DEEP33 user. Groq can return HTTP 429 when its organization limit is exhausted. Cloudflare currently documents 10,000 free Neurons per day; the original `@cf/meta/llama-3.1-8b-instruct` model was deprecated on May 30, 2026, so the configured default is `@cf/meta/llama-3.1-8b-instruct-fp8`. Check the current model catalog, quota, and billing configuration before enabling. To keep DEEP33 at $0, use a Workers Free plan with no paid overage enabled; requests beyond the free allowance must fail rather than incur charges. Keep this feature disabled until those conditions are confirmed.

This code does not magically guarantee provider capacity or unlimited free inference. When the free provider is unavailable, it fails quickly and the existing fallback chain continues. Current primary Edge behavior is unchanged while the feature flag is off.

## Validation checklist

1. Run `python -m pytest tests -q`.
2. Check the Supabase Edge Function logs for provider name, status, and elapsed time only; no token values.
3. Test cold and warm requests, stream-first-chunk latency, HTTP 429/5xx failover, and cancellation.
4. Verify that a stream never restarts on a second provider after output has been sent.
5. Confirm actual free-tier usage and total charges remain $0 before enabling the flag in production.
