package cl.caggrometal.deep33

import androidx.test.ext.junit.runners.AndroidJUnit4
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import java.net.HttpURLConnection
import java.net.URL

@RunWith(AndroidJUnit4::class)
class Deep33ConnectivityTest {
    private val baseUrl = "https://deep33-backend.onrender.com"

    @Test
    fun backendHealthIsPass() {
        val json = get("/health")
        assertTrue(json.optString("status") == "PASS")
        assertTrue(json.optString("version") == "0.1.3")
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
        val connection = open("POST", "/v1/ai/generate")
        connection.setRequestProperty("Content-Type", "application/json")
        connection.doOutput = true
        val payload = JSONObject()
            .put("messages", JSONArray().put(JSONObject()
                .put("role", "user")
                .put("content", "Return only this exact token: DEEP33_ANDROID_E2E_OK")))
        connection.outputStream.use { it.write(payload.toString().toByteArray(Charsets.UTF_8)) }
        val code = connection.responseCode
        val body = (if (code in 200..299) connection.inputStream else connection.errorStream)
            .bufferedReader(Charsets.UTF_8).use { it.readText() }
        connection.disconnect()
        assertTrue("chat HTTP $code: $body", code in 200..299)
        val content = JSONObject(body).getJSONObject("response")
            .getJSONArray("choices").getJSONObject(0)
            .getJSONObject("message").getString("content")
        assertTrue(content.contains("DEEP33_ANDROID_E2E_OK"))
    }

    @Test
    fun streamEndpointResponds() {
        val connection = open("POST", "/v1/chat/stream")
        connection.setRequestProperty("Content-Type", "application/json")
        connection.setRequestProperty("Accept", "text/event-stream")
        connection.doOutput = true
        val payload = JSONObject()
            .put("messages", JSONArray().put(JSONObject()
                .put("role", "user")
                .put("content", "Return only this exact token: DEEP33_STREAM_E2E_OK")))
        connection.outputStream.use { it.write(payload.toString().toByteArray(Charsets.UTF_8)) }
        val code = connection.responseCode
        val contentType = connection.contentType.orEmpty()
        connection.disconnect()
        assertTrue("stream HTTP $code", code in 200..299)
        assertTrue("stream content-type: $contentType", contentType.contains("text/event-stream"))
    }

    private fun get(path: String): JSONObject {
        val connection = open("GET", path)
        val code = connection.responseCode
        val body = (if (code in 200..299) connection.inputStream else connection.errorStream)
            .bufferedReader(Charsets.UTF_8).use { it.readText() }
        connection.disconnect()
        assertTrue("GET $path HTTP $code: $body", code in 200..299)
        return JSONObject(body)
    }

    private fun open(method: String, path: String): HttpURLConnection =
        (URL(baseUrl + path).openConnection() as HttpURLConnection).apply {
            requestMethod = method
            connectTimeout = 15000
            readTimeout = 90000
            useCaches = false
            setRequestProperty("Accept", "application/json")
        }
}
