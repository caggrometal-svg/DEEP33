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
        urls.forEach { endpoint ->
            val host = URL(endpoint).host
            val addresses = InetAddress.getAllByName(host)
            assertTrue("DNS resolution failed for " + host, addresses.isNotEmpty())
        }
    }

    @Test
    fun backendHealthIsPass() {
        val json = Deep33Api.get("/health", sessionId)
        assertTrue(json.optString("status") == "PASS")
        assertTrue(json.optString("version") == "0.2.0")
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
    fun realChatReturnsE2EToken() {
        val payload = JSONArray().put(
            JSONObject().put("role", "user")
                .put("content", "Return only this exact token: DEEP33_ANDROID_E2E_OK")
        )
        val body = Deep33Api.generate(payload, sessionId)
        val content = body.optJSONObject("result")?.optString("text").orEmpty()
        assertTrue("chat response: " + body, content.contains("DEEP33_ANDROID_E2E_OK"))
    }

    @Test
    fun streamEndpointReturnsRealToken() {
        val payload = JSONArray().put(
            JSONObject().put("role", "user")
                .put("content", "Return only this exact token: DEEP33_STREAM_E2E_OK")
        )
        val combined = StringBuilder()
        val result = Deep33Api.stream(
            payload,
            sessionId,
            onText = { combined.append(it) }
        )
        assertTrue("stream result: " + result, combined.contains("DEEP33_STREAM_E2E_OK"))
    }

    private fun get(path: String): JSONObject =
        Deep33Api.get(path, sessionId)

    @Test
    fun remoteMemoryContainsPersistedConversation() {
        if (BuildConfig.DEEP33_PRIMARY_URL.endsWith(".invalid")) return

        val payload = JSONArray().put(
            JSONObject().put("role", "user")
                .put("content", "Return only this exact token: DEEP33_MEMORY_E2E_OK")
        )

        val generated = Deep33Api.generate(payload, sessionId)
        val generatedText = generated.optJSONObject("result")?.optString("text").orEmpty()
        assertTrue("memory generation response: " + generated, generatedText.contains("DEEP33_MEMORY_E2E_OK"))

        val context = Deep33Api.memoryContext(sessionId)
        val messages = context.optJSONArray("messages") ?: JSONArray()

        var found = false
        for (i in 0 until messages.length()) {
            if (messages.optJSONObject(i)?.optString("content").orEmpty().contains("DEEP33_MEMORY_E2E_OK")) {
                found = true
                break
            }
        }

        assertTrue("persisted memory token missing: " + context, found)
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
