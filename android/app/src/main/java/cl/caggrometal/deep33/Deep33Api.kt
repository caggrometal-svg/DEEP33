package cl.caggrometal.deep33

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.net.SocketTimeoutException
import java.net.URI
import java.net.URL
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap
import javax.net.ssl.HttpsURLConnection

class Deep33ApiException(
    val kind: Kind,
    val statusCode: Int? = null,
    cause: Throwable? = null,
    val partialOutput: Boolean = false
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

object Deep33FailoverPolicy {
    fun canFailover(
        method: String,
        requestBodyStarted: Boolean,
        error: Deep33ApiException.Kind,
        idempotentRequest: Boolean = false
    ): Boolean = when (error) {
        Deep33ApiException.Kind.SERVER,
        Deep33ApiException.Kind.NETWORK,
        Deep33ApiException.Kind.TIMEOUT ->
            !method.equals("POST", ignoreCase = true) ||
                !requestBodyStarted ||
                idempotentRequest
        Deep33ApiException.Kind.AUTH,
        Deep33ApiException.Kind.BAD_RESPONSE ->
            !method.equals("POST", ignoreCase = true) ||
                !requestBodyStarted ||
                idempotentRequest
        Deep33ApiException.Kind.RATE_LIMIT,
        Deep33ApiException.Kind.CANCELLED -> false
    }
}

object Deep33Api {
    @Volatile private var authContext: Context? = null
    @Volatile private var authProfileId: String? = null

    fun configureAuthProfile(context: Context, profileId: String) {
        authContext = context.applicationContext
        authProfileId = profileId.trim()
        Deep33Auth.configure(context)
    }

    private fun authHeaders(profileId: String?): Pair<String?, String?> {
        val context = authContext ?: return null to null
        val localProfileId = profileId?.trim()?.takeIf { it.isNotBlank() } ?: authProfileId
        if (localProfileId.isNullOrBlank()) return null to null
        val session = Deep33Auth.ensureSession(context, localProfileId)
        return session.accessToken to session.memoryProfileId
    }

    private const val GLOBAL_TIMEOUT_MS = 180_000
    private const val MEMORY_SYNC_TIMEOUT_MS = 8_000L
    private const val CONNECT_TIMEOUT_MS = 15_000
    private const val FAST_HEALTH_TIMEOUT_MS = 20_000
    private const val FAST_HEALTH_CONNECT_TIMEOUT_MS = 4_000
    // Generation recovery is centralized in Deep33GenerationService. Each API call
    // therefore performs one attempt per configured endpoint; no nested transport retry.
    private const val ENDPOINT_ATTEMPTS = 1

    private fun validateEndpoint(
        raw: String,
        allowIsolatedTestEndpoint: Boolean = false,
    ): String? {
        val candidate = raw.trim().trimEnd('/')
        if (candidate.isBlank()) return null
        return runCatching {
            val uri = URI(candidate)
            val scheme = uri.scheme?.lowercase()
            val host = uri.host?.trim().orEmpty()
            val testOnlyHost = allowIsolatedTestEndpoint && BuildConfig.DEBUG && (
                host == "127.0.0.1" ||
                    host == "::1" ||
                    host.endsWith(".invalid")
                )
            if (
                scheme != "https" ||
                host.isBlank() ||
                uri.userInfo != null ||
                uri.query != null ||
                uri.fragment != null ||
                (!testOnlyHost && uri.port != -1 && uri.port != 443) ||
                !testOnlyHost && host != host.lowercase()
            ) {
                null
            } else {
                candidate
            }
        }.getOrNull()
    }

    private fun canonicalEndpoints(): List<String> = listOf(
        BuildConfig.DEEP33_PRIMARY_URL,
        BuildConfig.DEEP33_SECONDARY_URL,
        BuildConfig.DEEP33_TERTIARY_URL,
    ).mapNotNull(::validateEndpoint).distinct()

    private fun normalizedEndpoints(overrides: List<String>? = null): List<String> {
        val canonical = canonicalEndpoints()
        if (overrides == null) return canonical

        // Production/release transport can never be redirected to an arbitrary HTTPS host.
        // Debug instrumentation may inject only isolated loopback/.invalid endpoints to
        // simulate transport loss; these cannot become real external destinations.
        val requested = overrides.asSequence()
            .mapNotNull { raw ->
                validateEndpoint(raw) ?: validateEndpoint(raw, allowIsolatedTestEndpoint = true)
            }
            .distinct()
            .toList()
        if (requested.isEmpty() || requested.size != overrides.distinct().size) return emptyList()

        val nonCanonical = requested.filter { it !in canonical }
        if (nonCanonical.isNotEmpty()) {
            if (!BuildConfig.DEBUG) return emptyList()
            if (nonCanonical.any { validateEndpoint(it, allowIsolatedTestEndpoint = true) == null }) {
                return emptyList()
            }
        }
        return requested
    }


    // One transport registry per request. A global single connection can cancel or
    // disconnect the wrong generation when a stale lifecycle callback arrives.
    private val activeStreamConnections = ConcurrentHashMap<String, HttpsURLConnection>()

    fun get(path: String, sessionId: String): JSONObject =
        request("GET", path, null, sessionId)

    fun getFast(path: String, sessionId: String): JSONObject =
        request(
            "GET",
            path,
            null,
            sessionId,
            timeoutMs = FAST_HEALTH_TIMEOUT_MS.toLong(),
            connectTimeoutMs = FAST_HEALTH_CONNECT_TIMEOUT_MS.toLong()
        )

    fun generate(
        messages: JSONArray,
        sessionId: String,
        personality: String = "NEUTRO",
        requestId: String = UUID.randomUUID().toString(),
        idempotencyKey: String = requestId,
        endpointOverride: List<String>? = null,
        acceptResponse: ((JSONObject) -> Boolean)? = null
    ): JSONObject =
        request(
            "POST",
            "/v1/ai/generate",
            JSONObject().put("messages", messages).put("personality", personality),
            sessionId,
            requestId,
            idempotencyKey,
            endpointOverride
        )

    fun memoryContext(sessionId: String, memoryProfileId: String? = null): JSONObject =
        request(
            "GET",
            "/v1/memory/context",
            null,
            sessionId,
            acceptResponse = { payload ->
                payload.optJSONObject("session") != null ||
                    payload.optJSONArray("messages")?.length().orZero() > 0 ||
                    payload.optJSONArray("memories")?.length().orZero() > 0
            },
            memoryProfileId = memoryProfileId
        )

    private fun Int?.orZero(): Int = this ?: 0

    fun syncMemory(
        sessionId: String,
        messages: JSONArray,
        personality: String = "NEUTRO",
        requestId: String = UUID.randomUUID().toString(),
        memoryProfileId: String? = null,
        timeoutMs: Long = MEMORY_SYNC_TIMEOUT_MS,
    ): JSONObject =
        request(
            "POST",
            "/v1/memory/sync",
            JSONObject()
                .put("messages", messages)
                .put("personality", personality),
            sessionId,
            requestId,
            "memory-sync-" + requestId,
            memoryProfileId = memoryProfileId,
            timeoutMs = timeoutMs,
        )

    fun remember(sessionId: String, kind: String, content: String, memoryProfileId: String? = null): JSONObject =
        request(
            "POST",
            "/v1/memory/remember",
            JSONObject().put("kind", kind).put("content", content),
            sessionId,
            memoryProfileId = memoryProfileId
        )

    fun submitFeedback(
        sessionId: String,
        rating: String,
        responseText: String,
        memoryProfileId: String? = null,
    ): JSONObject {
        val normalizedRating = rating.trim().lowercase()
        require(normalizedRating == "useful" || normalizedRating == "not_useful") {
            "Invalid DEEP33 feedback rating"
        }
        val digest = java.security.MessageDigest.getInstance("SHA-256")
            .digest(responseText.toByteArray(Charsets.UTF_8))
            .joinToString("") { "%02x".format(it) }

        return request(
            "POST",
            "/v1/feedback",
            JSONObject()
                .put("rating", normalizedRating)
                .put("response_hash", digest),
            sessionId,
            idempotencyKey = "feedback:" + digest + ":" + normalizedRating,
            endpointOverride = listOf(BuildConfig.DEEP33_PRIMARY_URL),
            memoryProfileId = memoryProfileId,
            timeoutMs = 5_000L,
        )
    }

    fun setPreferences(
        sessionId: String,
        personality: String,
        preferences: JSONObject = JSONObject(),
        memoryProfileId: String? = null
    ): JSONObject =
        request(
            "PUT",
            "/v1/memory/preferences",
            JSONObject()
                .put("personality", personality)
                .put("preferences", preferences),
            sessionId,
            memoryProfileId = memoryProfileId
        )

    fun stream(
        messages: JSONArray,
        sessionId: String,
        personality: String = "NEUTRO",
        requestId: String = UUID.randomUUID().toString(),
        idempotencyKey: String = requestId,
        memoryProfileId: String? = null,
        timeoutMs: Long = GLOBAL_TIMEOUT_MS.toLong(),
        isCancelled: () -> Boolean = { false },
        onText: (String) -> Unit
    ): String {
        val activePersonality = personality.trim().uppercase().let {
            if (it in setOf("AGRESIVO", "NEUTRO", "COMICO", "CONSPIRANOICO")) it else "NEUTRO"
        }
        val boundedTimeoutMs = timeoutMs.coerceIn(1_000L, GLOBAL_TIMEOUT_MS.toLong())
        val deadline = System.nanoTime() + boundedTimeoutMs * 1_000_000L
        var lastError: Deep33ApiException? = null

        for (endpoint in normalizedEndpoints()) {
            var attempt = 0
            while (attempt < ENDPOINT_ATTEMPTS) {
                attempt++
                val remainingMs = ((deadline - System.nanoTime()) / 1_000_000L)
                if (remainingMs <= 250L) break

                val connection = URL(endpoint + "/v1/chat/stream").openConnection() as HttpsURLConnection
                activeStreamConnections[requestId] = connection
                val output = StringBuilder()
                var emitted = false
                var requestBodyStarted = false
                var sawDone = false
                var eventType: String? = null

                try {
                    connection.requestMethod = "POST"
                    connection.connectTimeout = minOf(CONNECT_TIMEOUT_MS.toLong(), remainingMs).toInt()
                    connection.readTimeout = remainingMs.toInt()
                    connection.useCaches = false
                    connection.doInput = true
                    connection.doOutput = true
                    connection.instanceFollowRedirects = false
                    connection.setRequestProperty("Accept", "text/event-stream")
                    connection.setRequestProperty("Accept-Encoding", "identity")
                    connection.setRequestProperty("Cache-Control", "no-cache")
                    connection.setRequestProperty("Content-Type", "application/json")
                    connection.setRequestProperty("X-DEEP33-Session-Id", sessionId)
                    val (accessToken, remoteMemoryProfileId) = authHeaders(memoryProfileId)
                        ?: throw Deep33ApiException(Deep33ApiException.Kind.AUTH)
                    connection.setRequestProperty("Authorization", "Bearer " + accessToken)
                    connection.setRequestProperty("X-DEEP33-Memory-Profile-Id", remoteMemoryProfileId)
                    connection.setRequestProperty("X-Request-ID", requestId)
                    connection.setRequestProperty("X-Idempotency-Key", idempotencyKey)
                    connection.setRequestProperty("X-DEEP33-Personality", activePersonality)

                    connection.connect()
                    requestBodyStarted = true
                    connection.outputStream.use {
                        it.write(
                            JSONObject()
                                .put("messages", messages)
                                .put("personality", activePersonality)
                                .toString()
                                .toByteArray(Charsets.UTF_8)
                        )
                    }

                    val code = connection.responseCode
                    if (code !in 200..299) throw mapError(code)
                    val acknowledgedPersonality =
                        connection.getHeaderField("X-DEEP33-Personality").orEmpty().trim().uppercase()
                    if (
                        acknowledgedPersonality.isNotBlank() &&
                        acknowledgedPersonality != activePersonality
                    ) {
                        throw Deep33ApiException(Deep33ApiException.Kind.BAD_RESPONSE, code)
                    }
                    if (acknowledgedPersonality.isNotBlank()) {
                        android.util.Log.i(
                            "DEEP33",
                            "PERSONALITY ACK: " + acknowledgedPersonality
                        )
                    }
                    if (!connection.contentType.orEmpty().contains("text/event-stream", ignoreCase = true)) {
                        throw Deep33ApiException(Deep33ApiException.Kind.BAD_RESPONSE, code)
                    }

                    connection.inputStream.bufferedReader(Charsets.UTF_8).useLines { lines ->
                        lines.forEach { line ->
                            if (isCancelled()) throw Deep33ApiException(Deep33ApiException.Kind.CANCELLED)
                            if (line.startsWith("event:")) {
                                eventType = line.substringAfter(":", "").trim()
                                return@forEach
                            }
                            if (line.isBlank()) {
                                eventType = null
                                return@forEach
                            }
                            if (!line.startsWith("data:")) return@forEach
                            val data = line.removePrefix("data:").trim()
                            if (data.isBlank()) return@forEach
                            if (eventType == "error") throw mapStreamError(data)
                            if (data == "[DONE]") {
                                sawDone = true
                                return@useLines
                            }
                            sawStreamData = true
                            val chunk = SseTextParser.extractText(data).orEmpty()
                            if (chunk.isNotEmpty()) {
                                emitted = true
                                output.append(chunk)
                                onText(chunk)
                            }
                        }
                    }
                    // A clean TCP/HTTP close is valid when the server omitted [DONE].
                    // A stream with no data at all remains invalid.
                    if (!sawDone && !sawStreamData && output.isEmpty()) {
                        throw Deep33ApiException(Deep33ApiException.Kind.BAD_RESPONSE)
                    }
                    return output.toString()
                } catch (e: Deep33ApiException) {
                    lastError = e
                    if (
                        e.kind == Deep33ApiException.Kind.CANCELLED ||
                        !Deep33FailoverPolicy.canFailover("POST", requestBodyStarted, e.kind, idempotentRequest = true)
                    ) {
                        throw if (emitted && !e.partialOutput) {
                            Deep33ApiException(e.kind, e.statusCode, e.cause, partialOutput = true)
                        } else {
                            e
                        }
                    }
                    if (attempt >= ENDPOINT_ATTEMPTS) break
                } catch (e: SocketTimeoutException) {
                    lastError = Deep33ApiException(Deep33ApiException.Kind.TIMEOUT, cause = e)
                    if (!Deep33FailoverPolicy.canFailover(
                            "POST",
                            requestBodyStarted,
                            Deep33ApiException.Kind.TIMEOUT,
                            idempotentRequest = true
                        )
                    ) {
                        throw if (emitted) {
                            Deep33ApiException(
                                Deep33ApiException.Kind.TIMEOUT,
                                cause = e,
                                partialOutput = true
                            )
                        } else {
                            lastError
                        }
                    }
                    if (attempt >= ENDPOINT_ATTEMPTS) break
                } catch (e: IOException) {
                    lastError = if (isCancelled()) {
                        Deep33ApiException(Deep33ApiException.Kind.CANCELLED, cause = e)
                    } else {
                        Deep33ApiException(Deep33ApiException.Kind.NETWORK, cause = e)
                    }
                    if (
                        lastError.kind == Deep33ApiException.Kind.CANCELLED ||
                        !Deep33FailoverPolicy.canFailover("POST", requestBodyStarted, lastError.kind, idempotentRequest = true)
                    ) {
                        throw if (emitted && !lastError.partialOutput) {
                            Deep33ApiException(
                                lastError.kind,
                                lastError.statusCode,
                                lastError.cause,
                                partialOutput = true
                            )
                        } else {
                            lastError
                        }
                    }
                    if (attempt >= ENDPOINT_ATTEMPTS) break
                } finally {
                    activeStreamConnections.remove(requestId, connection)
                    connection.disconnect()
                }
            }
        }

        throw lastError ?: Deep33ApiException(Deep33ApiException.Kind.NETWORK)
    }

    fun cancelActiveStream(requestId: String?) {
        val key = requestId?.trim().orEmpty()
        if (key.isBlank()) return
        activeStreamConnections.remove(key)?.disconnect()
    }

    private fun mapStreamError(data: String): Deep33ApiException {
        val code = try {
            JSONObject(data).optString("code")
        } catch (_: Exception) {
            ""
        }
        return when (code) {
            "AI_GATEWAY_TIMEOUT" -> Deep33ApiException(Deep33ApiException.Kind.TIMEOUT, 504)
            "AI_GATEWAY_HTTP_ERROR" -> Deep33ApiException(Deep33ApiException.Kind.SERVER, 502)
            "AI_GATEWAY_INVALID_RESPONSE" -> Deep33ApiException(Deep33ApiException.Kind.BAD_RESPONSE, 502)
            else -> Deep33ApiException(Deep33ApiException.Kind.BAD_RESPONSE)
        }
    }

    internal fun mapError(code: Int): Deep33ApiException = Deep33ApiException(
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
        idempotencyKey: String = requestId,
        endpointOverride: List<String>? = null,
        acceptResponse: ((JSONObject) -> Boolean)? = null,
        memoryProfileId: String? = null,
        timeoutMs: Long = GLOBAL_TIMEOUT_MS.toLong(),
        connectTimeoutMs: Long = CONNECT_TIMEOUT_MS.toLong()
    ): JSONObject {
        val deadline = System.nanoTime() + timeoutMs * 1_000_000L
        var lastError: Deep33ApiException? = null

        for (endpoint in normalizedEndpoints(endpointOverride)) {
            var attempt = 0
            while (attempt < ENDPOINT_ATTEMPTS) {
                attempt++
                val remainingMs = ((deadline - System.nanoTime()) / 1_000_000L)
                if (remainingMs <= 250L) break

                val url = URL(endpoint + path)
                var requestBodyStarted = false
                val connection = url.openConnection() as HttpsURLConnection

                try {
                    connection.requestMethod = method
                    connection.connectTimeout = minOf(connectTimeoutMs, remainingMs).toInt()
                    connection.readTimeout = remainingMs.toInt()
                    connection.useCaches = false
                    connection.doInput = true
                    connection.instanceFollowRedirects = false
                    connection.setRequestProperty("Accept", "application/json")
                    connection.setRequestProperty("X-DEEP33-Session-Id", sessionId)
                    val (accessToken, remoteMemoryProfileId) = authHeaders(memoryProfileId)
                        ?: throw Deep33ApiException(Deep33ApiException.Kind.AUTH)
                    connection.setRequestProperty("Authorization", "Bearer " + accessToken)
                    connection.setRequestProperty("X-DEEP33-Memory-Profile-Id", remoteMemoryProfileId)
                    connection.setRequestProperty("X-Request-ID", requestId)
                    connection.setRequestProperty("X-Idempotency-Key", idempotencyKey)

                    if (body != null) {
                        requestBodyStarted = true
                        connection.doOutput = true
                        connection.setRequestProperty("Content-Type", "application/json")
                    }

                    // Configure the request completely before connecting. Android's
                    // HttpsURLConnection rejects doOutput changes after connect().
                    connection.connect()

                    if (body != null) {
                        connection.outputStream.use {
                            it.write(body.toString().toByteArray(Charsets.UTF_8))
                        }
                    }

                    val code = connection.responseCode
                    if (code !in 200..299) throw mapError(code)
                    val responseContentType = connection.contentType.orEmpty()
                    if (!responseContentType.contains("json", ignoreCase = true)) {
                        throw Deep33ApiException(Deep33ApiException.Kind.BAD_RESPONSE, code)
                    }

                    val payload = connection.inputStream.bufferedReader(Charsets.UTF_8).use { it.readText() }
                    val json = try {
                        JSONObject(payload)
                    } catch (e: Exception) {
                        throw Deep33ApiException(Deep33ApiException.Kind.BAD_RESPONSE, code, e)
                    }

                    // A memory context endpoint can legally return HTTP 200 while
                    // exposing no session/messages/memories because that endpoint
                    // is backed by a stale or empty upstream. Treat that response
                    // as unusable so the next failover endpoint can serve the
                    // current remote memory store.
                    if (acceptResponse != null && !acceptResponse(json)) {
                        lastError = Deep33ApiException(
                            Deep33ApiException.Kind.BAD_RESPONSE,
                            code
                        )
                        break
                    }

                    return json
                } catch (e: Deep33ApiException) {
                    lastError = e
                    if (!Deep33FailoverPolicy.canFailover(
                            method,
                            requestBodyStarted,
                            e.kind,
                            idempotentRequest = body != null && idempotencyKey.isNotBlank()
                        )) throw e
                    if (attempt >= ENDPOINT_ATTEMPTS) break
                } catch (e: SocketTimeoutException) {
                    lastError = Deep33ApiException(Deep33ApiException.Kind.TIMEOUT, cause = e)
                    if (!Deep33FailoverPolicy.canFailover(
                            method,
                            requestBodyStarted,
                            Deep33ApiException.Kind.TIMEOUT,
                            idempotentRequest = body != null && idempotencyKey.isNotBlank()
                        )) throw lastError
                    if (attempt >= ENDPOINT_ATTEMPTS) break
                } catch (e: IOException) {
                    lastError = Deep33ApiException(Deep33ApiException.Kind.NETWORK, cause = e)
                    if (!Deep33FailoverPolicy.canFailover(
                            method,
                            requestBodyStarted,
                            Deep33ApiException.Kind.NETWORK,
                            idempotentRequest = body != null && idempotencyKey.isNotBlank()
                        )) throw lastError
                    if (attempt >= ENDPOINT_ATTEMPTS) break
                } finally {
                    connection.disconnect()
                }
            }
        }

        throw lastError ?: Deep33ApiException(Deep33ApiException.Kind.NETWORK)
    }
}

object SseTextParser {
    fun extractText(data: String): String? {
        return runCatching {
            val json = JSONObject(data)
            extractChoiceContent(json.optJSONArray("choices")?.optJSONObject(0))
        }.getOrNull()
    }

    private fun extractChoiceContent(choice: JSONObject?): String? {
        if (choice == null) return null
        extractContentValue(choice.opt("delta")?.let { it as? JSONObject }?.opt("content"))
            ?.takeIf { it.isNotEmpty() }
            ?.let { return it }

        extractContentValue(choice.opt("message")?.let { it as? JSONObject }?.opt("content"))
            ?.takeIf { it.isNotEmpty() }
            ?.let { return it }

        // Some providers expose content directly on the choice envelope.
        return extractContentValue(choice.opt("content"))
    }

    private fun extractContentValue(value: Any?): String? {
        return when (value) {
            is String -> value.takeIf { it.isNotEmpty() }
            is JSONArray -> {
                val out = StringBuilder()
                for (i in 0 until value.length()) {
                    val item = value.opt(i)
                    when (item) {
                        is String -> out.append(item)
                        is JSONObject -> {
                            val type = item.optString("type").lowercase()
                            if (type.isBlank() || type == "text" || type == "output_text") {
                                out.append(
                                    item.optString("text").ifBlank {
                                        item.optString("content")
                                    }
                                )
                            }
                        }
                    }
                }
                out.toString().takeIf { it.isNotEmpty() }
            }
            is JSONObject -> {
                val type = value.optString("type").lowercase()
                if (type.isBlank() || type == "text" || type == "output_text") {
                    extractContentValue(value.opt("text"))
                        ?: extractContentValue(value.opt("content"))
                } else {
                    null
                }
            }
            else -> null
        }
    }
}