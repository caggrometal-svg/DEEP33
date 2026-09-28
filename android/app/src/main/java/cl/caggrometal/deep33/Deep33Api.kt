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
    enum class Kind { NETWORK, TIMEOUT, AUTH, RATE_LIMIT, SERVER, BAD_RESPONSE }

    companion object {
        private fun messageFor(kind: Kind): String = when (kind) {
            Kind.NETWORK -> "Sin conexión con DEEP33."
            Kind.TIMEOUT -> "DEEP33 no respondió a tiempo."
            Kind.AUTH -> "El acceso al backend fue rechazado."
            Kind.RATE_LIMIT -> "El servicio alcanzó temporalmente su límite."
            Kind.SERVER -> "El servicio de DEEP33 está temporalmente no disponible."
            Kind.BAD_RESPONSE -> "DEEP33 devolvió una respuesta inválida."
        }
    }
}

object Deep33Api {
    const val BASE_URL = "https://deep33-backend.onrender.com"

    fun get(path: String, sessionId: String): JSONObject =
        request("GET", path, null, sessionId)

    fun generate(messages: JSONArray, sessionId: String): JSONObject =
        request(
            "POST",
            "/v1/ai/generate",
            JSONObject().put("messages", messages),
            sessionId
        )

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
