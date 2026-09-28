package cl.caggrometal.deep33

import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.net.SocketTimeoutException
import java.net.URL
import java.util.UUID
import javax.net.ssl.HttpsURLConnection

class Deep33ApiException(
    val kind: Kind,
    val statusCode: Int? = null,
    cause: Throwable? = null
) : IOException(messageFor(kind), cause) {
    enum class Kind { NETWORK, TIMEOUT, AUTH, RATE_LIMIT, SERVER, BAD_RESPONSE, CANCELLED }

    companion object {
        private fun messageFor(kind: Kind): String = when (kind) {
            Kind.NETWORK -> "Sin conexión con DEEP33."
            Kind.TIMEOUT -> "DEEP33 no respondió a tiempo."
            Kind.AUTH -> "El acceso al backend fue rechazado."
            Kind.RATE_LIMIT -> "El servicio alcanzó temporalmente su límite."
            Kind.SERVER -> "El servicio de DEEP33 está temporalmente no disponible."
            Kind.BAD_RESPONSE -> "DEEP33 devolvió una respuesta inválida."
            Kind.CANCELLED -> "Generación cancelada."
        }
    }
}

object Deep33Api {
    private const val GLOBAL_TIMEOUT_MS = 90_000
    private const val CONNECT_TIMEOUT_MS = 15_000

    private fun endpoints(): List<String> = listOf(
        BuildConfig.DEEP33_PRIMARY_URL,
        BuildConfig.DEEP33_SECONDARY_URL
    ).map { it.trim().trimEnd('/') }
        .filter { it.isNotBlank() && it.startsWith("https://") }
        .distinct()

    private fun shouldFailover(error: Deep33ApiException): Boolean =
        error.kind == Deep33ApiException.Kind.NETWORK ||
            error.kind == Deep33ApiException.Kind.TIMEOUT ||
            error.kind == Deep33ApiException.Kind.SERVER
    @Volatile
    private var activeStreamConnection: HttpsURLConnection? = null

    fun get(path: String, sessionId: String): JSONObject =
        request("GET", path, null, sessionId)

    fun generate(
        messages: JSONArray,
        sessionId: String,
        personality: String = "NEUTRO",
        requestId: String = UUID.randomUUID().toString(),
        idempotencyKey: String = requestId
    ): JSONObject =
        request(
            "POST",
            "/v1/ai/generate",
            JSONObject().put("messages", messages).put("personality", personality),
            sessionId,
            requestId,
            idempotencyKey
        )

    fun memoryContext(sessionId: String): JSONObject =
        request("GET", "/v1/memory/context", null, sessionId)

    fun remember(sessionId: String, kind: String, content: String): JSONObject =
        request(
            "POST",
            "/v1/memory/remember",
            JSONObject().put("kind", kind).put("content", content),
            sessionId
        )


    fun setPreferences(
        sessionId: String,
        personality: String,
        preferences: JSONObject = JSONObject()
    ): JSONObject =
        request(
            "PUT",
            "/v1/memory/preferences",
            JSONObject()
                .put("personality", personality)
                .put("preferences", preferences),
            sessionId
        )

    fun stream(
        messages: JSONArray,
        sessionId: String,
        personality: String = "NEUTRO",
        requestId: String = UUID.randomUUID().toString(),
        idempotencyKey: String = requestId,
        isCancelled: () -> Boolean = { false },
        onText: (String) -> Unit
    ): String {
        val deadline = System.nanoTime() + GLOBAL_TIMEOUT_MS * 1_000_000L
        var lastError: Deep33ApiException? = null

        for (endpoint in endpoints()) {
            val remainingMs = ((deadline - System.nanoTime()) / 1_000_000L).coerceAtLeast(3_000L)
            if (remainingMs <= 3_000L) break
            val connection = URL(endpoint + "/v1/chat/stream").openConnection() as HttpsURLConnection
            activeStreamConnection = connection
            val output = StringBuilder()
            var emitted = false
            try {
                connection.requestMethod = "POST"
                connection.connectTimeout = minOf(CONNECT_TIMEOUT_MS.toLong(), remainingMs).toInt()
                connection.readTimeout = remainingMs.toInt()
                connection.useCaches = false
                connection.doInput = true
                connection.doOutput = true
                connection.instanceFollowRedirects = false
                connection.setRequestProperty("Accept", "text/event-stream")
                connection.setRequestProperty("Content-Type", "application/json")
                connection.setRequestProperty("X-DEEP33-Session-Id", sessionId)
                connection.setRequestProperty("X-Request-ID", requestId)
                connection.setRequestProperty("X-Idempotency-Key", idempotencyKey)
                connection.outputStream.use {
                    it.write(JSONObject().put("messages", messages).put("personality", personality).toString().toByteArray(Charsets.UTF_8))
                }

                val code = connection.responseCode
                if (code !in 200..299) throw mapError(code)
                if (!connection.contentType.orEmpty().contains("text/event-stream", ignoreCase = true)) {
                    throw Deep33ApiException(Deep33ApiException.Kind.BAD_RESPONSE, code)
                }

                connection.inputStream.bufferedReader(Charsets.UTF_8).useLines { lines ->
                    lines.forEach { line ->
                        if (isCancelled()) throw Deep33ApiException(Deep33ApiException.Kind.CANCELLED)
                        val data = line.removePrefix("data:").trim()
                        if (data.isBlank()) return@forEach
                        if (data == "[DONE]") return@useLines
                        if (line.startsWith("event: error")) return@forEach
                        val chunk = SseTextParser.extractText(data).orEmpty()
                        if (chunk.isNotEmpty()) {
                            emitted = true
                            output.append(chunk)
                            onText(chunk)
                        }
                    }
                }
                return output.toString()
            } catch (e: Deep33ApiException) {
                lastError = e
                if (e.kind == Deep33ApiException.Kind.CANCELLED || emitted || !shouldFailover(e)) throw e
            } catch (e: SocketTimeoutException) {
                lastError = Deep33ApiException(Deep33ApiException.Kind.TIMEOUT, cause = e)
                if (emitted) throw lastError
            } catch (e: IOException) {
                lastError = if (isCancelled()) Deep33ApiException(Deep33ApiException.Kind.CANCELLED, cause = e)
                else Deep33ApiException(Deep33ApiException.Kind.NETWORK, cause = e)
                if (lastError.kind == Deep33ApiException.Kind.CANCELLED || emitted) throw lastError
            } finally {
                if (activeStreamConnection === connection) activeStreamConnection = null
                connection.disconnect()
            }
        }
        throw lastError ?: Deep33ApiException(Deep33ApiException.Kind.NETWORK)
    }

    fun cancelActiveStream() {
        activeStreamConnection?.disconnect()
    }

    private fun mapError(code: Int): Deep33ApiException = Deep33ApiException(
        when (code) {
            401, 403 -> Deep33ApiException.Kind.AUTH
            429 -> Deep33ApiException.Kind.RATE_LIMIT
            in 500..599 -> Deep33ApiException.Kind.SERVER
            else -> Deep33ApiException.Kind.BAD_RESPONSE
        },
        code
    )

    private fun request(
        method: String,
        path: String,
        body: JSONObject?,
        sessionId: String,
        requestId: String = UUID.randomUUID().toString(),
        idempotencyKey: String = requestId
    ): JSONObject {
        val deadline = System.nanoTime() + GLOBAL_TIMEOUT_MS * 1_000_000L
        var lastError: Deep33ApiException? = null

        for (endpoint in endpoints()) {
            val remainingMs = ((deadline - System.nanoTime()) / 1_000_000L).coerceAtLeast(3_000L)
            if (remainingMs <= 3_000L) break
            val url = URL(endpoint + path)
            val connection = url.openConnection() as HttpsURLConnection
            try {
                connection.requestMethod = method
                connection.connectTimeout = minOf(CONNECT_TIMEOUT_MS.toLong(), remainingMs).toInt()
                connection.readTimeout = remainingMs.toInt()
                connection.useCaches = false
                connection.doInput = true
                connection.instanceFollowRedirects = false
                connection.setRequestProperty("Accept", "application/json")
                connection.setRequestProperty("X-DEEP33-Session-Id", sessionId)
                connection.setRequestProperty("X-Request-ID", requestId)
                connection.setRequestProperty("X-Idempotency-Key", idempotencyKey)
                if (body != null) {
                    connection.doOutput = true
                    connection.setRequestProperty("Content-Type", "application/json")
                    connection.outputStream.use { it.write(body.toString().toByteArray(Charsets.UTF_8)) }
                }
                val code = connection.responseCode
                if (code !in 200..299) throw mapError(code)
                val payload = connection.inputStream.bufferedReader(Charsets.UTF_8).use { it.readText() }
                return try {
                    JSONObject(payload)
                } catch (e: Exception) {
                    throw Deep33ApiException(Deep33ApiException.Kind.BAD_RESPONSE, code, e)
                }
            } catch (e: Deep33ApiException) {
                lastError = e
                if (!shouldFailover(e)) throw e
            } catch (e: SocketTimeoutException) {
                lastError = Deep33ApiException(Deep33ApiException.Kind.TIMEOUT, cause = e)
            } catch (e: IOException) {
                lastError = Deep33ApiException(Deep33ApiException.Kind.NETWORK, cause = e)
            } finally {
                connection.disconnect()
            }
        }
        throw lastError ?: Deep33ApiException(Deep33ApiException.Kind.NETWORK)
    }

}

object SseTextParser {
    fun extractText(data: String): String? {
        return try {
            val json = JSONObject(data)
            val choices = json.optJSONArray("choices") ?: return null
            val first = choices.optJSONObject(0) ?: return null

            val delta = first.optJSONObject("delta")
            val deltaContent = delta?.optString("content").orEmpty()
            if (deltaContent.isNotEmpty()) return deltaContent

            val messageContent = first.optJSONObject("message")?.optString("content").orEmpty()
            messageContent.ifEmpty { null }
        } catch (_: Exception) {
            null
        }
    }
}
