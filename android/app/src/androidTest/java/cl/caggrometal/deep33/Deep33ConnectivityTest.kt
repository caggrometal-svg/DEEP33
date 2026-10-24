package cl.caggrometal.deep33

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import java.net.InetAddress
import java.net.HttpURLConnection
import java.net.URL
import java.util.UUID
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

@RunWith(AndroidJUnit4::class)
class Deep33ConnectivityTest {
    private val baseUrl = BuildConfig.DEEP33_PRIMARY_URL
    private val sessionId = "android-e2e-" + UUID.randomUUID()

    @Before
    fun configureAuthenticatedApi() {
        // Instrumentation tests call Deep33Api directly without launching MainActivity.
        // Configure the per-profile auth manager so all production endpoints receive a JWT.
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        Deep33Api.configureAuth(context)
        val args = InstrumentationRegistry.getArguments()
        val e2eUrl = args.getString("deep33_e2e_vercel_url").orEmpty().trim()
        if (e2eUrl.isNotBlank()) {
            val oidcToken = args.getString("deep33_e2e_vercel_oidc_token")
            Deep33Api.configureE2EBackend(e2eUrl, oidcToken)
            println("ANDROID_E2E_BACKEND_CONFIGURED host=" + java.net.URL(e2eUrl).host)
        }
    }

    @Test
    fun allFailoverEndpointHostsResolveInsideAndroid() {
        val urls = listOf(
            Deep33Api.e2eEndpointUrl(),
            BuildConfig.DEEP33_VERCEL_URL,
            BuildConfig.DEEP33_PRIMARY_URL,
            BuildConfig.DEEP33_SECONDARY_URL,
            BuildConfig.DEEP33_TERTIARY_URL
        )
        urls.filter { it.isNotBlank() }.forEach { endpoint ->
            val host = URL(endpoint).host
            var resolved = false
            var dnsError: Throwable? = null
            repeat(3) { attempt ->
                if (resolved) return@repeat
                val executor = Executors.newSingleThreadExecutor()
                try {
                    val future = executor.submit<Array<InetAddress>> {
                        InetAddress.getAllByName(host)
                    }
                    val addresses = future.get(8, TimeUnit.SECONDS)
                    resolved = addresses.isNotEmpty()
                } catch (error: Throwable) {
                    dnsError = error
                    if (attempt < 2) Thread.sleep(500)
                } finally {
                    executor.shutdownNow()
                }
            }
            assertTrue(
                "DNS resolution failed for " + host + " after bounded retries: " +
                    (dnsError?.message ?: "unknown"),
                resolved
            )
        }
    }

    @Test
    fun vercelPrimaryHealthIsVerifiedWithoutFailover() {
        val json = Deep33Api.get(
            "/health",
            sessionId,
            endpointOverride = listOf(Deep33Api.e2eEndpointUrl())
        )
        assertTrue("Vercel health: " + json, json.optString("status") == "PASS")
        assertTrue("Vercel service was not directly reached: " + json, json.optString("service") == "DEEP33 Backend")
        assertTrue("Vercel version missing: " + json, json.optString("version").isNotBlank())
        assertTrue("Vercel build id missing: " + json, json.optString("build_id").isNotBlank())
        assertTrue("Vercel timestamp missing: " + json, json.optString("timestamp").isNotBlank())
        println("ANDROID_VERCEL_DIRECT_HEALTH_PASS service=${json.optString("service")} build_id=${json.optString("build_id")}")
    }

    @Test
    fun primaryHealthMatchesCanonicalBackendContract() {
        val json = Deep33Api.get("/health", sessionId)
        assertTrue("backend health: " + json, json.optString("status") == "PASS")

        when (json.optString("service")) {
            "DEEP33 Backend" -> {
                // Vercel is the canonical Android endpoint; it reports the FastAPI
                // backend contract, not the older Supabase edge-gateway contract.
                assertTrue("backend version missing: " + json, json.optString("version").isNotBlank())
                assertTrue("backend build id missing: " + json, json.optString("build_id").isNotBlank())
                assertTrue("backend timestamp missing: " + json, json.optString("timestamp").isNotBlank())
            }
            "DEEP33 Edge Gateway" -> {
                // Keep compatibility with the Supabase edge endpoint when failover
                // selected it after the canonical Vercel endpoint was unavailable.
                assertTrue("edge runtime: " + json, json.optString("runtime") == "supabase-edge")
                assertTrue("edge transport: " + json, json.optString("transport") == "https")
                assertTrue(
                    "edge failover support: " + json,
                    json.optBoolean("failover_supported", false)
                )
            }
            else -> assertTrue("unexpected backend health contract: " + json, false)
        }
    }

    @Test
    fun vercelPrimaryLiveSearchExtractsMultipleSourcesWithoutFailover() {
        val query = "Busca en Internet cuál es la versión estable más reciente de Python publicada oficialmente. Contrasta las páginas oficiales recuperadas y cita enlaces directos."
        val encodedQuery = java.net.URLEncoder.encode(query, "UTF-8")
        val requestStartedAt = System.nanoTime()
        val json = Deep33Api.get(
            "/v1/web/search?q=" + encodedQuery,
            sessionId,
            endpointOverride = listOf(Deep33Api.e2eEndpointUrl())
        )
        val elapsedMs = TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - requestStartedAt)

        assertTrue("Vercel live search was not successful: " + json, json.optBoolean("ok", false))
        assertTrue("Vercel live search was not realtime: " + json, json.optBoolean("realtime", false))
        val results = json.optJSONArray("results") ?: JSONArray()
        val pages = json.optJSONArray("fetched_pages") ?: JSONArray()
        val domainsUsed = json.optInt("domains_used", 0)
        val extractedChars = json.optInt("extracted_text_chars", 0)
        val providers = json.optJSONArray("providers") ?: JSONArray()
        assertTrue("Vercel search used fewer than two independent providers: " + json, providers.length() >= 2)
        assertTrue("Vercel search returned fewer than two results: " + json, results.length() >= 2)
        assertTrue("Vercel search extracted fewer than two pages: " + json, pages.length() >= 2)
        assertTrue("Vercel search used fewer than two domains: " + json, domainsUsed >= 2)
        assertTrue("Vercel extraction was too short: " + json, extractedChars >= 80)

        val summaries = buildString {
            for (index in 0 until results.length()) {
                val item = results.optJSONObject(index) ?: continue
                append(' ')
                append(item.optString("title"))
                append(' ')
                append(item.optString("url"))
                append(' ')
                append(item.optString("snippet"))
            }
        }
        assertTrue(
            "Vercel results were not relevant to the Python query: " + json,
            summaries.contains("python", ignoreCase = true)
        )
        assertTrue("Vercel search exceeded the 180-second deadline: ${elapsedMs}ms", elapsedMs < 180_000)
        println(
            "ANDROID_VERCEL_LIVE_SEARCH_PASS results=${results.length()} pages=${pages.length()} " +
                "domains=${domainsUsed} extracted_chars=${extractedChars} total_ms=${elapsedMs}"
        )
    }

    @Test
    fun diagnosticsAreOnline() {
        val json = Deep33Api.get("/v1/ai/diagnostics", sessionId)
        assertTrue(json.optBoolean("online", false))
        val checks = json.getJSONObject("checks")
        assertTrue(checks.optString("NETWORK") == "PASS")
        assertTrue(checks.optString("AI_GATEWAY") == "PASS")
        assertTrue(checks.optString("MODEL") == "PASS")
    }


    @Test
    fun connectivityAuditConfirmsInternetAndSearch() {
        val json = Deep33Api.get("/v1/connectivity/audit", sessionId)
        assertTrue("connectivity audit: " + json, json.optString("edge") == "PASS")
        assertTrue("internet audit: " + json, json.optString("internet") == "PASS")
        assertTrue("search audit: " + json, json.optJSONObject("search")?.optBoolean("ok", false) == true)
    }

    @Test
    fun realChatReturnsE2EEnvelope() {
        val payload = JSONArray().put(
            JSONObject().put("role", "user")
                .put("content", "Provide a brief connectivity response for Android E2E certification.")
        )
        val body = Deep33Api.generate(payload, sessionId)
        assertGenerationEnvelope(body, "chat")
    }

    @Test
    fun realAndroidTransportFallsBackFromProtectedVercelPreviewToLivePrimary() {
        val payload = JSONArray().put(
            JSONObject().put("role", "user")
                .put("content", "Respond with one short sentence confirming backend connectivity.")
        )
        val requestId = UUID.randomUUID().toString()
        val startedAt = System.nanoTime()
        val body = Deep33Api.generate(
            messages = payload,
            sessionId = sessionId,
            requestId = requestId,
            idempotencyKey = requestId,
            endpointOverride = listOf(
                Deep33Api.e2eEndpointUrl(),
                BuildConfig.DEEP33_PRIMARY_URL
            )
        )
        val elapsedMs = TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - startedAt)
        assertGenerationEnvelope(body, "vercel-preview-fallback-to-primary")
        assertTrue("Vercel-to-primary failover exceeded 180 seconds: ${elapsedMs}ms", elapsedMs < 180_000)
        println(
            "ANDROID_REAL_VERCEL_FALLBACK_PASS preview_first=true primary_fallback=available total_ms=${elapsedMs}"
        )
    }

    @Test
    fun realAndroidTransportFailsOverFromUnavailableFirstEndpointToLiveBackend() {
        val payload = JSONArray().put(
            JSONObject().put("role", "user")
                .put("content", "Respond with one short sentence confirming backend connectivity.")
        )
        val requestId = UUID.randomUUID().toString()
        val requestStartedAt = System.nanoTime()
        val body = Deep33Api.generate(
            messages = payload,
            sessionId = sessionId,
            requestId = requestId,
            idempotencyKey = requestId,
            endpointOverride = listOf(
                "https://deep33-e2e-unreachable.invalid",
                BuildConfig.DEEP33_PRIMARY_URL
            )
        )
        val elapsedMs = TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - requestStartedAt)
        assertGenerationEnvelope(body, "real-failover")
        assertTrue("real failover exceeded the 180-second deadline: ${elapsedMs}ms", elapsedMs < 180_000)
        println("ANDROID_REAL_FAILOVER_PASS unreachable_primary=confirmed live_fallback=confirmed total_ms=${elapsedMs}")
    }

    @Test
    fun repeatedCurrentWebQuestionsUseSearchSourcesAndMemory() {
        if (BuildConfig.DEEP33_PRIMARY_URL.endsWith(".invalid")) return

        val prompts = listOf(
            "Busca en internet las últimas noticias de Chile hoy y responde brevemente con fuentes reales.",
            "Busca en internet el clima actual de Santiago de Chile y responde brevemente con fuentes reales.",
            "Busca en internet información actual de tecnología en Chile hoy y responde brevemente con fuentes reales."
        )

        prompts.forEachIndexed { index, prompt ->
            val payload = JSONArray().put(
                JSONObject().put("role", "user").put("content", prompt)
            )
            val requestStartedAt = System.nanoTime()
            val body = Deep33Api.generate(payload, sessionId)
            val generationMs = TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - requestStartedAt)
            assertTrue(
                "current-web round ${index + 1} exceeded the 180-second generation deadline: ${generationMs}ms",
                generationMs < 180_000
            )
            assertGenerationEnvelope(body, "current-web-round-${index + 1}")
            assertTrue(
                "web navigation was not used in round ${index + 1}: $body",
                body.optBoolean("web_navigation", false)
            )
            val sources = body.optJSONArray("sources") ?: JSONArray()
            assertTrue(
                "insufficient web sources in round ${index + 1}: $body",
                sources.length() >= 2
            )

            var persisted = false
            repeat(6) {
                val context = Deep33Api.memoryContext(sessionId)
                val messages = context.optJSONArray("messages") ?: JSONArray()
                for (messageIndex in 0 until messages.length()) {
                    if (messages.optJSONObject(messageIndex)?.optString("content").orEmpty().contains(prompt)) {
                        persisted = true
                        break
                    }
                }
                if (persisted) return@repeat
                Thread.sleep(500)
            }
            assertTrue(
                "current web round ${index + 1} was not persisted in memory",
                persisted
            )
            println("ANDROID_CURRENT_WEB_E2E_PASS round=${index + 1} sources=${sources.length()} total_ms=${generationMs}")
        }
    }

    @Test
    fun streamEndpointReturnsData() {
        val payload = JSONArray().put(
            JSONObject().put("role", "user")
                .put("content", "Provide a brief streaming connectivity response for Android E2E certification.")
        )
        val combined = StringBuilder()
        val streamStartedAt = System.nanoTime()
        var firstTextMs: Long? = null
        val result = Deep33Api.stream(
            payload,
            sessionId,
            onText = {
                if (firstTextMs == null) {
                    firstTextMs = TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - streamStartedAt)
                }
                combined.append(it)
            }
        )
        val totalStreamMs = TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - streamStartedAt)
        val measuredFirstTextMs = firstTextMs
            ?: throw AssertionError("stream callback emitted no first visible text")
        assertTrue(
            "first visible text exceeded the 12-second deadline: ${measuredFirstTextMs}ms",
            measuredFirstTextMs < 12_000
        )
        assertTrue("stream exceeded the 180-second deadline: ${totalStreamMs}ms", totalStreamMs < 180_000)
        println("ANDROID_STREAM_TIMING ttft_ms=${measuredFirstTextMs} total_ms=${totalStreamMs}")
        assertTrue("stream result is empty: " + result, combined.toString().isNotBlank())
        assertTrue("stream callback result mismatch", result == combined.toString())

        val normalized = result.replace(Regex("\\s+"), " ").trim()
        val duplicatedHalf = normalized.length % 2 == 0 &&
            normalized.substring(0, normalized.length / 2) ==
            normalized.substring(normalized.length / 2)
        val repeatedFallback =
            normalized.windowed(
                "El servicio de DEEP33 está temporalmente fuera de servicio".length,
                1,
                partialWindows = false
            ).count { it == "El servicio de DEEP33 está temporalmente fuera de servicio" } > 1 ||
            normalized.windowed(
                "Servicio temporalmente no disponible".length,
                1,
                partialWindows = false
            ).count { it == "Servicio temporalmente no disponible" } > 1

        assertTrue("duplicated complete streaming response: " + normalized, !duplicatedHalf)
        assertTrue("duplicated fallback streaming response: " + normalized, !repeatedFallback)
        println("ANDROID_DUPLICATES=0")
    }

    @Test
    fun vercelFinanceStreamReturnsCurrentSourceBackedChileanDollar() {
        val query = "Valor del dólar en Chile hoy. Indica el valor en pesos chilenos, fecha u hora de la cotización y la fuente."
        val payload = JSONArray().put(
            JSONObject().put("role", "user").put("content", query)
        )
        val requestId = UUID.randomUUID().toString()
        val startedAt = System.nanoTime()
        val answer = StringBuilder()
        var firstTextMs: Long? = null

        val result = Deep33Api.stream(
            messages = payload,
            sessionId = sessionId,
            requestId = requestId,
            idempotencyKey = requestId,
            onText = { chunk ->
                if (firstTextMs == null) {
                    firstTextMs = TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - startedAt)
                }
                answer.append(chunk)
            },
            endpointOverride = listOf(Deep33Api.e2eEndpointUrl())
        )
        val totalMs = TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - startedAt)
        val text = result.trim()
        assertTrue("Vercel finance stream returned no answer", text.isNotBlank())
        assertTrue("Vercel finance stream exceeded 12-second first-text deadline: ${firstTextMs}ms", (firstTextMs ?: Long.MAX_VALUE) < 12_000)
        assertTrue("Vercel finance stream exceeded 180-second deadline: ${totalMs}ms", totalMs < 180_000)
        assertTrue("Current quote lacks Chilean peso units: " + text, text.contains("pesos chilenos", ignoreCase = true))

        val quoteMatch = Regex(
            "(?i)(?:=|es)\\s*([0-9][0-9.,]*)\\s*pesos chilenos"
        ).find(text) ?: throw AssertionError("Current quote number is missing: " + text)
        val quoteRaw = quoteMatch.groupValues[1]
        val normalizedQuote = when {
            quoteRaw.contains(",") -> quoteRaw.replace(".", "").replace(",", ".")
            quoteRaw.count { it == '.' } > 1 -> quoteRaw.replace(".", "")
            quoteRaw.contains(".") && quoteRaw.substringAfterLast(".").length == 3 -> quoteRaw.replace(".", "")
            else -> quoteRaw
        }
        val quoteValue = normalizedQuote.toDoubleOrNull()
            ?: throw AssertionError("Quote could not be parsed: " + quoteRaw)
        assertTrue("Quote outside plausible CLP-per-USD bounds: " + quoteRaw, quoteValue in 50.0..10_000.0)

        val urls = Regex("https://[^\\s)]+")
            .findAll(text)
            .map { it.value.trimEnd('.', ',', ';') }
            .distinct()
            .toList()
        val domains = urls.mapNotNull { url ->
            runCatching { java.net.URL(url).host.lowercase() }.getOrNull()
        }.toSet()
        assertTrue("Current quote has no HTTPS source URL: " + text, urls.isNotEmpty())
        val hasStructuredFinanceProvider = domains.any {
            it == "query1.finance.yahoo.com" || it == "mindicador.cl"
        }
        assertTrue(
            "Quote source was not verified by the finance provider or multiple independent web domains: " + text,
            hasStructuredFinanceProvider || domains.size >= 2
        )
        assertTrue(
            "Quote does not contain market timestamp or observation date: " + text,
            Regex("\\b20\\d{2}-\\d{2}-\\d{2}\\b").containsMatchIn(text) ||
                text.contains("Hora de mercado reportada", ignoreCase = true)
        )
        println(
            "ANDROID_VERCEL_FINANCE_STREAM_PASS quote=${quoteRaw} domains=${domains.size} " +
                "ttft_ms=${firstTextMs} total_ms=${totalMs}"
        )
    }

    private fun get(path: String): JSONObject =
        Deep33Api.get(path, sessionId)

    @Test
    fun remoteMemoryContainsPersistedConversation() {
        if (BuildConfig.DEEP33_PRIMARY_URL.endsWith(".invalid")) return

        val memoryToken = "DEEP33_MEMORY_E2E_OK"
        val memoryConversation = JSONArray()
            .put(
                JSONObject()
                    .put("role", "user")
                    .put("content", memoryToken)
            )
            .put(
                JSONObject()
                    .put("role", "assistant")
                    .put("content", "Stored memory marker: " + memoryToken)
            )

        val synced = Deep33Api.syncMemory(sessionId, memoryConversation, "NEUTRO")
        assertTrue(
            "memory sync failed: " + synced,
            synced.optBoolean("ok", synced.optString("status").isBlank() || synced.optString("status") == "PASS")
        )

        val context = Deep33Api.memoryContext(sessionId)
        val messages = context.optJSONArray("messages") ?: JSONArray()
        var found = false
        for (i in 0 until messages.length()) {
            if (messages.optJSONObject(i)?.optString("content").orEmpty().contains(memoryToken)) {
                found = true
                break
            }
        }
        assertTrue("persisted memory token missing: " + context, found)

        val appContext = androidx.test.platform.app.InstrumentationRegistry
            .getInstrumentation()
            .targetContext
        val recreatedStore = SessionStore(appContext)
        recreatedStore.activateSession(sessionId)
        assertTrue(
            "session id was not restored after recreation",
            recreatedStore.sessionId == sessionId
        )

        val afterRecreation = Deep33Api.memoryContext(recreatedStore.sessionId)
        val restoredMessages = afterRecreation.optJSONArray("messages") ?: JSONArray()
        var restored = false
        for (i in 0 until restoredMessages.length()) {
            if (restoredMessages.optJSONObject(i)?.optString("content").orEmpty().contains(memoryToken)) {
                restored = true
                break
            }
        }
        assertTrue(
            "memory was not recoverable after session recreation: " + afterRecreation,
            restored
        )
    }

    private fun assertGenerationEnvelope(body: JSONObject, label: String) {
        val result = body.optJSONObject("result")
        assertTrue(label + " result missing: " + body, result != null)
        assertTrue(label + " role invalid: " + body, result?.optString("role") == "assistant")
        assertTrue(label + " text empty: " + body, result?.optString("text").orEmpty().isNotBlank())
    }


    private fun open(method: String, path: String, sessionId: String? = null): HttpURLConnection =
        (URL(baseUrl + path).openConnection() as HttpURLConnection).apply {
            requestMethod = method
            connectTimeout = 15000
            readTimeout = 90000
            useCaches = false
            setRequestProperty("Accept", "application/json")
            if (!sessionId.isNullOrBlank()) {
                setRequestProperty("X-DEEP33-Session-Id", sessionId)
            }
        }
}
