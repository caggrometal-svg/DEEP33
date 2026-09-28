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
        val json = get("/health")
        assertTrue(json.optString("status") == "PASS")
        assertTrue(json.optString("version") == "0.2.0")
    }

    @Test
    fun diagnosticsAreOnline() {
        val json = get("/v1/ai/diagnostics")
        assertTrue(json.optBoolean("online", false))
        val checks = json.getJSONObject("checks")
        assertTrue(checks.optString("NETWORK") == "PASS")
        assertTrue(checks.optString("AI_GATEWAY") == "PASS")
        assertTrue(checks.optString("MODEL") == "PASS")
    }

    @Test
    fun realChatReturnsE2EToken() {
        val connection = open("POST", "/v1/ai/generate", sessionId)
        connection.setRequestProperty("Content-Type", "application/json")
        connection.doOutput = true
        val payload = JSONObject().put("messages", JSONArray().put(
            JSONObject().put("role", "user").put("content", "Return only this exact token: DEEP33_ANDROID_E2E_OK")
        ))
        connection.outputStream.use { it.write(payload.toString().toByteArray(Charsets.UTF_8)) }
        val code = connection.responseCode
        val body = (if (code in 200..299) connection.inputStream else connection.errorStream)
            .bufferedReader(Charsets.UTF_8).use { it.readText() }
        connection.disconnect()
        assertTrue("chat HTTP " + code + ": " + body, code in 200..299)
        val content = JSONObject(body).getJSONObject("response")
            .getJSONArray("choices").getJSONObject(0).getJSONObject("message").getString("content")
        assertTrue(content.contains("DEEP33_ANDROID_E2E_OK"))
    }

    @Test
    fun streamEndpointReturnsRealToken() {
        val connection = open("POST", "/v1/chat/stream", sessionId)
        connection.setRequestProperty("Content-Type", "application/json")
        connection.setRequestProperty("Accept", "text/event-stream")
        connection.doOutput = true

        val payload = JSONObject().put("messages", JSONArray().put(
            JSONObject().put(
                "role",
                "user"
            ).put(
                "content",
                "Return only this exact token: DEEP33_STREAM_E2E_OK"
            )
        ))
        connection.outputStream.use { it.write(payload.toString().toByteArray(Charsets.UTF_8)) }

        val code = connection.responseCode
        assertTrue("stream HTTP " + code, code in 200..299)
        assertTrue(
            "unexpected content-type: " + connection.contentType,
            connection.contentType.orEmpty().contains("text/event-stream", ignoreCase = true)
        )

        val reader = connection.inputStream.bufferedReader(Charsets.UTF_8)
        val combined = StringBuilder()
        try {
            while (true) {
                val line = reader.readLine() ?: break
                if (!line.startsWith("data:")) continue

                val data = line.removePrefix("data:").trim()
                if (data == "[DONE]") break
                if (data.isBlank()) continue

                val json = JSONObject(data)
                val choices = json.optJSONArray("choices") ?: continue
                val first = choices.optJSONObject(0) ?: continue
                val delta = first.optJSONObject("delta") ?: continue
                val chunk = delta.optString("content")
                if (chunk.isNotEmpty()) {
                    combined.append(chunk)
                    if (combined.contains("DEEP33_STREAM_E2E_OK")) break
                }
            }
        } finally {
            reader.close()
            connection.disconnect()
        }

        assertTrue(
            "stream token missing: " + combined,
            combined.contains("DEEP33_STREAM_E2E_OK")
        )
    }

    private fun get(path: String): JSONObject {
        val connection = open("GET", path)
        val code = connection.responseCode
        val body = (if (code in 200..299) connection.inputStream else connection.errorStream)
            .bufferedReader(Charsets.UTF_8).use { it.readText() }
        connection.disconnect()
        assertTrue("GET " + path + " HTTP " + code + ": " + body, code in 200..299)
        return JSONObject(body)
    }

    @Test
    fun remoteMemoryContainsPersistedConversation() {
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
