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
    private const val GLOBAL_TIMEOUT_MS = 180_000
    @Volatile private var authContext: Context? = null

    fun configureAuth(context: Context) {
        authContext = context.applicationContext
    }

    private fun authorizationToken(forceRefresh: Boolean = false): String? {
        val context = authContext ?: return null
        val profileId = MultiUserIdentity.currentProfileId(context)
        return runCatching {
            val manager = SupabaseAuthManager(context, profileId)
            if (forceRefresh) manager.forceRefreshSession() else manager.ensureSession()
        }.getOrNull()
    }

    private fun refreshAuthorizationToken(): Boolean =
        !authorizationToken(forceRefresh = true).isNullOrBlank()
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
        )

    fun sendFeedback(
        sessionId: String,
        requestId: String,
        rating: String,
        responseHash: String,
    ): JSONObject =
        request(
            "POST",
            "/v1/feedback",
            JSONObject()
                .put("rating", rating)
                .put("request_id", requestId)
                .put("response_hash", responseHash),
            sessionId,
            requestId = "feedback-" + requestId,
            idempotencyKey = "feedback-" + requestId + "-" + rating,
            timeoutMs = 5_000L,
        )

    fun remember(sessionId: String, kind: String, content: String, memoryProfileId: String? = null): JSONObject =
        request(
            "POST",
            "/v1/memory/remember",
            JSONObject().put("kind", kind).put("content", content),
            sessionId,
            memoryProfileId = memoryProfileId
        )

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
        isCancelled: () -> Boolean = { false },
        onText: (String) -> Unit,
        deadlineAtNanos: Long? = null
    ): String {
        val activePersonality = personality.trim().uppercase().let {
            if (it in setOf("AGRESIVO", "NEUTRO", "COMICO", "CONSPIRANOICO")) it else "NEUTRO"
        }
        val deadline = deadlineAtNanos ?: (System.nanoTime() + GLOBAL_TIMEOUT_MS * 1_000_000L)
        var lastError: Deep33ApiException? = null

        for (endpoint in normalizedEndpoints()) {
            var attempt = 0
            var authRetryUsed = false
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
                    // Remote ownership is derived from the authenticated Supabase subject.
                    // The legacy client-provided memory-profile header is intentionally no
                    // longer authoritative and is not sent to the server.
                    authorizationToken(forceRefresh = authRetryUsed)
                        ?.let { connection.setRequestProperty("Authorization", "Bearer " + it) }
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
                            val chunk = SseTextParser.extractText(data).orEmpty()
                            if (chunk.isNotEmpty()) {
                                emitted = true
                                output.append(chunk)
                                onText(chunk)
                            }
                        }
                    }
                    // clean SSE termination: a valid stream may end by EOF without an explicit
                    // [DONE] after text has been received; an actually empty answer remains invalid.
                    return output.toString()
                } catch (e: Deep33ApiException) {
                    lastError = e
                    if (
                        e.kind == Deep33ApiException.Kind.AUTH &&
                        !authRetryUsed &&
                        refreshAuthorizationToken()
                    ) {
                        authRetryUsed = true
                        attempt -= 1
                        continue
                    }
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
            var authRetryUsed = false
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
                    authorizationToken(forceRefresh = authRetryUsed)
                        ?.let { connection.setRequestProperty("Authorization", "Bearer " + it) }
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
                    if (
                        e.kind == Deep33ApiException.Kind.AUTH &&
                        !authRetryUsed &&
                        refreshAuthorizationToken()
                    ) {
                        authRetryUsed = true
                        attempt -= 1
                        continue
                    }
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
            val root = JSONObject(data)

            fun contentValue(value: Any?): String? {
                when (value) {
                    is String -> return value
                    is JSONArray -> {
                        val out = StringBuilder()
                        for (i in 0 until value.length()) {
                            val item = value.opt(i)
                            val piece = when (item) {
                                is String -> item
                                is JSONObject -> item.optString("text").ifBlank {
                                    item.optString("content")
                                }
                                else -> ""
                            }
                            if (piece.isNotBlank()) out.append(piece)
                        }
                        return out.toString().ifBlank { null }
                    }
                    is JSONObject -> {
                        return value.optString("text").ifBlank {
                            value.optString("content")
                        }.ifBlank { null }
                    }
                }
                return null
            }

            val rootContent = contentValue(root.opt("content"))
            if (!rootContent.isNullOrBlank()) return rootContent

            val choices = root.optJSONArray("choices") ?: return null
            val first = choices.optJSONObject(0) ?: return null

            val deltaContent = contentValue(first.optJSONObject("delta")?.opt("content"))
            if (!deltaContent.isNullOrBlank()) return deltaContent

            val message = first.optJSONObject("message")
            val messageContent = contentValue(message?.opt("content"))
            if (!messageContent.isNullOrBlank()) return messageContent

            contentValue(first.opt("content"))
        }.getOrNull()
    }
}
