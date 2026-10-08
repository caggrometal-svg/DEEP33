# DEEP33 Phase 0 — Baseline

Reference APK
- File: `DEEP33-MULTIUSUARIO-OPTIMIZADA.apk`
- Version: 0.5.0
- SHA-256: `1339ff1671d523728056a210c5b1fc1c85a00ddfbc5b1df091c38c6af624cccb`

Reference source
- Repository: `caggrometal-svg/DEEP33`
- Branch: `main`
- Commit: `2dd4b040e9018d4a9f7e054f88f6529276781e4d`
- Workflow: `DEEP33 Current APK #151`
- Run ID: `37445361289`
- Run result: `success`
- Version source: `android/app/build.gradle.kts` (`versionName = 0.5.0`)

Binary note

The uploaded APK and the GitHub Actions APK artifact are not byte-identical. The uploaded SHA-256 is therefore treated as the functional artifact under audit, while commit `2dd4b040e9018d4a9f7e054f88f6529276781e4d` is the exact source reference recovered from GitHub Actions.

Code baseline findings before the current repair branch

Generation
- `Deep33GenerationService` allowed up to 6 stream attempts.
- Each `Deep33Api.stream()` previously owned a fresh 180 s transport deadline.
- The recovery loop therefore had no single global deadline and could extend far beyond 180 s.
- Retry backoff was `1 s + 2 s + 4 s + 8 s + 16 s`.
- Streaming checkpoints called `saveGenerationState(... durable = true)`, which reached synchronous `SharedPreferences.commit()` from the stream callback.
- Completed generations synchronously called `Deep33Api.syncMemory()` before releasing the service wake lock.

State and recovery
- Generation state was represented by independent Activity and Service booleans plus persisted enum state.
- Cancellation cleared persistent markers immediately from Activity instead of first publishing a durable terminal cancellation state.
- `loadRemoteContext()` checked `generationActive` after replacing and saving the conversation snapshot.

Multiuser
- `SessionStore.memoryProfileId` was stable at installation scope.
- The same profile id was reused across sessions, so local profile isolation was not a real per-user identity boundary.

GPS/privacy
- Location context used exact latitude/longitude in the inference prompt.
- Android location resolution selected the first modern provider callback instead of ranking fresh candidates by age and accuracy.
- Local-query detection did not cover natural weather requests such as “¿Hará calor hoy?” before resolving location.
- The generation service built its remote-memory payload from the inference payload, creating a path where exact GPS context could enter persisted remote conversation memory.

SSE
- The Android parser accepted `delta.content` and string `message.content`.
- It did not accept content arrays or root-level content envelopes.
- A clean stream close without explicit `[DONE]` was treated as an invalid response.

Search/diagnostics
- The main connectivity audit already had some parallel probes, but the Activity-side full diagnostic path was sequential.
- Public web search already had parallel provider requests, an inflight map, and standard-query caching.
- Realtime/local search semantics still relied heavily on narrow keyword detection.

Context
- Android conversation persistence was capped at 50 messages.
- Model-context selection used fixed fast/balanced/deep windows rather than a dynamic token/character budget with compaction.

Security
- `supabase/functions/deep33-proxy` and `deep33-tertiary` were deployed with JWT verification disabled in the live Supabase project.
- The proxy trusted client-provided session/profile headers for memory scope.
- No authenticated user subject was propagated from Supabase Auth into DEEP33 memory ownership.

Instrumentation baseline
The repository already exposed these latency markers:
`T0_INPUT`, `T1_REQUEST_SENT`, `T2_BACKEND_RECEIVED`, `T3_CONTEXT_PREPARED`, `T4_SEARCH_STARTED`, `T5_SEARCH_FINISHED`, `T6_INFERENCE_STARTED`, `T7_FIRST_TOKEN`, `T8_STREAM_FINISHED`, `T9_PERSISTENCE_FINISHED`, `T10_FIRST_VISIBLE`, `T11_FIRST_SPOKEN`.

The repository also contains backend p50/p95 latency snapshots. A true device-side TTFB/TTFC/memory runtime measurement still requires executing the instrumented APK on a real Android device/emulator; source inspection alone cannot provide those runtime numbers honestly.

Current repair branch
`doctor/deep33-architecture-speed-multiuser-20261008`

Completed in Lote A
- A shared 180 s generation deadline is now propagated across all stream recovery attempts.
- Retry sleep is bounded by the remaining global deadline.
- SSE generation checkpoints are coalesced and flushed asynchronously through a dedicated executor.
- The normal streaming path no longer calls synchronous `commit()` for checkpoints.
- Remote memory synchronization is moved to a wake-lock-free coordinator backed by the existing durable queue.
