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
    }

    @Test
    fun allFailoverEndpointHostsResolveInsideAndroid() {
        val urls = listOf(
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
            val body = Deep33Api.generate(payload, sessionId)
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
            println("ANDROID_CURRENT_WEB_E2E_PASS round=${index + 1} sources=${sources.length()}")
        }
    }

    @Test
    fun streamEndpointReturnsData() {
        val payload = JSONArray().put(
            JSONObject().put("role", "user")
                .put("content", "Provide a brief streaming connectivity response for Android E2E certification.")
        )
        val combined = StringBuilder()
        val result = Deep33Api.stream(
            payload,
            sessionId,
            onText = { combined.append(it) }
        )
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
