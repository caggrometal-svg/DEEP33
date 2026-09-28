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
    const val BASE_URL = "https://deep33-backend.onrender.com"

    @Volatile
    private var activeStreamConnection: HttpsURLConnection? = null

    fun get(path: String, sessionId: String): JSONObject =
        request("GET", path, null, sessionId)

    fun generate(messages: JSONArray, sessionId: String): JSONObject =
        request(
            "POST",
            "/v1/ai/generate",
            JSONObject().put("messages", messages),
            sessionId
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

    fun stream(
        messages: JSONArray,
        sessionId: String,
        isCancelled: () -> Boolean = { false },
        onText: (String) -> Unit
    ): String {
        val connection = URL(BASE_URL + "/v1/chat/stream").openConnection() as HttpsURLConnection
        activeStreamConnection = connection
        val output = StringBuilder()

        try {
            connection.requestMethod = "POST"
            connection.connectTimeout = 15_000
            connection.readTimeout = 90_000
            connection.useCaches = false
            connection.doInput = true
            connection.doOutput = true
            connection.instanceFollowRedirects = false
            connection.setRequestProperty("Accept", "text/event-stream")
            connection.setRequestProperty("Content-Type", "application/json")
            connection.setRequestProperty("X-DEEP33-Session-Id", sessionId)
            connection.setRequestProperty("X-Request-ID", UUID.randomUUID().toString())
            connection.outputStream.use {
                it.write(JSONObject().put("messages", messages).toString().toByteArray(Charsets.UTF_8))
            }

            val code = try {
                connection.responseCode
            } catch (e: SocketTimeoutException) {
                throw Deep33ApiException(Deep33ApiException.Kind.TIMEOUT, cause = e)
            } catch (e: IOException) {
                if (isCancelled()) throw Deep33ApiException(Deep33ApiException.Kind.CANCELLED, cause = e)
                throw Deep33ApiException(Deep33ApiException.Kind.NETWORK, cause = e)
            }

            if (code !in 200..299) {
                val kind = when (code) {
                    401, 403 -> Deep33ApiException.Kind.AUTH
                    429 -> Deep33ApiException.Kind.RATE_LIMIT
                    in 500..599 -> Deep33ApiException.Kind.SERVER
                    else -> Deep33ApiException.Kind.BAD_RESPONSE
                }
                throw Deep33ApiException(kind, code)
            }

            if (!connection.contentType.orEmpty().contains("text/event-stream", ignoreCase = true)) {
                throw Deep33ApiException(Deep33ApiException.Kind.BAD_RESPONSE, code)
            }

            try {
                connection.inputStream.bufferedReader(Charsets.UTF_8).useLines { lines ->
                    lines.forEach { line ->
                        if (isCancelled()) throw Deep33ApiException(Deep33ApiException.Kind.CANCELLED)
                        val data = line.removePrefix("data:").trim()
                        if (data.isBlank()) return@forEach
                        if (data == "[DONE]") return@useLines

                        val chunk = SseTextParser.extractText(data).orEmpty()
                        if (chunk.isNotEmpty()) {
                            output.append(chunk)
                            onText(chunk)
                        }
                    }
                }
            } catch (e: Deep33ApiException) {
                throw e
            } catch (e: SocketTimeoutException) {
                throw Deep33ApiException(Deep33ApiException.Kind.TIMEOUT, cause = e)
            } catch (e: IOException) {
                if (isCancelled()) throw Deep33ApiException(Deep33ApiException.Kind.CANCELLED, cause = e)
                throw Deep33ApiException(Deep33ApiException.Kind.NETWORK, cause = e)
            }

            return output.toString()
        } finally {
            if (activeStreamConnection === connection) activeStreamConnection = null
            connection.disconnect()
        }
    }

    fun cancelActiveStream() {
        activeStreamConnection?.disconnect()
    }

    private fun request(
        method: String,
        path: String,
        body: JSONObject?,
        sessionId: String
    ): JSONObject {
        val url = URL(BASE_URL + path)
        if (url.protocol.lowercase() != "https") {
            throw Deep33ApiException(Deep33ApiException.Kind.BAD_RESPONSE)
        }

        val connection = url.openConnection() as HttpsURLConnection
        try {
            connection.requestMethod = method
            connection.connectTimeout = 15_000
            connection.readTimeout = 90_000
            connection.useCaches = false
            connection.doInput = true
            connection.instanceFollowRedirects = false
            connection.setRequestProperty("Accept", "application/json")
            connection.setRequestProperty("X-DEEP33-Session-Id", sessionId)
            connection.setRequestProperty("X-Request-ID", UUID.randomUUID().toString())

            if (body != null) {
                connection.doOutput = true
                connection.setRequestProperty("Content-Type", "application/json")
                connection.outputStream.use {
                    it.write(body.toString().toByteArray(Charsets.UTF_8))
                }
            }

            val code = try {
                connection.responseCode
            } catch (e: SocketTimeoutException) {
                throw Deep33ApiException(Deep33ApiException.Kind.TIMEOUT, cause = e)
            } catch (e: IOException) {
                throw Deep33ApiException(Deep33ApiException.Kind.NETWORK, cause = e)
            }

            val payload = try {
                val stream = if (code in 200..299) connection.inputStream else connection.errorStream
                stream?.bufferedReader(Charsets.UTF_8)?.use { it.readText() }.orEmpty()
            } catch (e: IOException) {
                throw Deep33ApiException(Deep33ApiException.Kind.NETWORK, code, e)
            }

            if (code !in 200..299) {
                val kind = when (code) {
                    401, 403 -> Deep33ApiException.Kind.AUTH
                    429 -> Deep33ApiException.Kind.RATE_LIMIT
                    in 500..599 -> Deep33ApiException.Kind.SERVER
                    else -> Deep33ApiException.Kind.BAD_RESPONSE
                }
                throw Deep33ApiException(kind, code)
            }

            return try {
                JSONObject(payload)
            } catch (e: Exception) {
                throw Deep33ApiException(Deep33ApiException.Kind.BAD_RESPONSE, code, e)
            }
        } finally {
            connection.disconnect()
        }
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
