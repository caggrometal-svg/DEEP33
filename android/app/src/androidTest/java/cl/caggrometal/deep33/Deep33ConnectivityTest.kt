package cl.caggrometal.deep33

import androidx.test.ext.junit.runners.AndroidJUnit4
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import java.net.InetAddress
import java.net.HttpURLConnection
import java.net.URL
import java.util.UUID

@RunWith(AndroidJUnit4::class)
class Deep33ConnectivityTest {
    private val baseUrl = BuildConfig.DEEP33_PRIMARY_URL
    private val sessionId = "android-e2e-" + UUID.randomUUID()

    @Test
    fun allFailoverEndpointHostsResolveInsideAndroid() {
        val urls = listOf(
            BuildConfig.DEEP33_PRIMARY_URL,
            BuildConfig.DEEP33_SECONDARY_URL,
            BuildConfig.DEEP33_TERTIARY_URL
        )
        urls.filter { it.isNotBlank() }.forEach { endpoint ->
            val host = URL(endpoint).host
            val addresses = InetAddress.getAllByName(host)
            assertTrue("DNS resolution failed for " + host, addresses.isNotEmpty())
        }
    }

    @Test
    fun edgeHealthIsPass() {
        val json = Deep33Api.get("/health", sessionId)
        assertTrue("edge health: " + json, json.optString("status") == "PASS")
        assertTrue("edge service: " + json, json.optString("service") == "DEEP33 Edge Gateway")
        assertTrue("edge runtime: " + json, json.optString("runtime") == "supabase-edge")
        assertTrue("edge transport: " + json, json.optString("transport") == "https")
        assertTrue(
            "edge failover support: " + json,
            json.optBoolean("failover_supported", false)
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
