package cl.caggrometal.deep33

import androidx.test.ext.junit.runners.AndroidJUnit4
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import java.net.HttpURLConnection
import java.net.URL
import java.util.UUID

@RunWith(AndroidJUnit4::class)
class Deep33ConnectivityTest {
    private val baseUrl = BuildConfig.DEEP33_PRIMARY_URL
    private val sessionId = "android-e2e-" + UUID.randomUUID()

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
        val connection = open("POST", "/v1/ai/generate", sessionId)
        connection.setRequestProperty("Content-Type", "application/json")
        connection.doOutput = true
        val payload = JSONObject().put("messages", JSONArray().put(
            JSONObject().put("role", "user").put("content", "Return only this exact token: DEEP33_MEMORY_E2E_OK")
        ))
        connection.outputStream.use { it.write(payload.toString().toByteArray(Charsets.UTF_8)) }
        val code = connection.responseCode
        val body = (if (code in 200..299) connection.inputStream else connection.errorStream)
            .bufferedReader(Charsets.UTF_8).use { it.readText() }
        connection.disconnect()
        assertTrue("memory generation HTTP " + code + ": " + body, code in 200..299)

        val context = open("GET", "/v1/memory/context", sessionId)
        val contextCode = context.responseCode
        val contextBody = (if (contextCode in 200..299) context.inputStream else context.errorStream)
            .bufferedReader(Charsets.UTF_8).use { it.readText() }
        context.disconnect()
        assertTrue("memory context HTTP " + contextCode + ": " + contextBody, contextCode in 200..299)

        val messages = JSONObject(contextBody).getJSONArray("messages")
        var found = false
        for (i in 0 until messages.length()) {
            if (messages.optJSONObject(i)?.optString("content").orEmpty().contains("DEEP33_MEMORY_E2E_OK")) {
                found = true
                break
            }
        }
        assertTrue("persisted memory token missing: " + contextBody, found)
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
