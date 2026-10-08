package cl.caggrometal.deep33

import android.content.Context
import android.os.Build
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import org.json.JSONObject
import java.io.BufferedReader
import java.io.InputStreamReader
import java.net.HttpURLConnection
import java.net.URL
import java.nio.charset.StandardCharsets
import java.util.Base64
import java.util.concurrent.ConcurrentHashMap

data class Deep33AuthSession(
    val accessToken: String,
    val refreshToken: String,
    val userId: String,
    val memoryProfileId: String,
    val expiresAtEpochSeconds: Long
) {
    fun valid(nowEpochSeconds: Long = System.currentTimeMillis() / 1000L): Boolean =
        accessToken.isNotBlank() &&
            refreshToken.isNotBlank() &&
            userId.isNotBlank() &&
            memoryProfileId.isNotBlank() &&
            expiresAtEpochSeconds - nowEpochSeconds > 60L
}

object Deep33Auth {
    private const val PREFS = "deep33_auth"
    private const val KEY_PREFIX = "session_"
    private const val KEY_ALIAS = "DEEP33_AUTH_AES_GCM_V1"
    private const val CONNECT_TIMEOUT_MS = 5_000
    private const val READ_TIMEOUT_MS = 10_000
    private const val MAX_RESPONSE_BYTES = 32_768
    private const val PROFILE_ID_PATTERN = "[A-Za-z0-9._-]{8,96}"

    @Volatile
    private var configuredContext: Context? = null

    private val cache = ConcurrentHashMap<String, Deep33AuthSession>()
    private val locks = ConcurrentHashMap<String, Any>()

    fun configure(context: Context) {
        configuredContext = context.applicationContext
    }

    fun currentSession(profileId: String): Deep33AuthSession? =
        cache[profileId.trim()]

    @Synchronized
    fun ensureSession(context: Context, profileId: String): Deep33AuthSession {
        configure(context)
        val id = profileId.trim()
        require(id.matches(Regex(PROFILE_ID_PATTERN))) { "Invalid DEEP33 profile id" }

        cache[id]?.takeIf { it.valid() }?.let { return it }

        val lock = locks.getOrPut(id) { Any() }
        synchronized(lock) {
            cache[id]?.takeIf { it.valid() }?.let { return it }
            val stored = loadEncrypted(context, id)
            if (stored != null && stored.valid()) {
                cache[id] = stored
                return stored
            }

            val refreshed = stored?.let {
                runCatching { broker(context, "refresh", id, it.refreshToken) }.getOrNull()
            }
            if (refreshed != null) {
                saveEncrypted(context, id, refreshed)
                cache[id] = refreshed
                return refreshed
            }

            val created = broker(context, "session", id, null)
            saveEncrypted(context, id, created)
            cache[id] = created
            return created
        }
    }

    fun remoteMemoryProfileId(context: Context, localProfileId: String): String =
        ensureSession(context, localProfileId).memoryProfileId

    private fun broker(
        context: Context,
        action: String,
        profileId: String,
        refreshToken: String?
    ): Deep33AuthSession {
        val endpoint = BuildConfig.DEEP33_AUTH_URL.trimEnd('/')
        require(endpoint.startsWith("https://")) { "DEEP33_AUTH_URL must use HTTPS" }

        val body = JSONObject()
            .put("action", action)
            .put("memory_profile_id", profileId)
        if (!refreshToken.isNullOrBlank()) {
            body.put("refresh_token", refreshToken)
        }

        val connection = (URL(endpoint).openConnection() as HttpURLConnection).apply {
            requestMethod = "POST"
            connectTimeout = CONNECT_TIMEOUT_MS
            readTimeout = READ_TIMEOUT_MS
            doOutput = true
            useCaches = false
            instanceFollowRedirects = false
            setRequestProperty("Content-Type", "application/json")
            setRequestProperty("Accept", "application/json")
            setRequestProperty("Cache-Control", "no-store")
            setRequestProperty("X-Request-ID", java.util.UUID.randomUUID().toString())
        }

        try {
            val bytes = body.toString().toByteArray(StandardCharsets.UTF_8)
            connection.outputStream.use { it.write(bytes) }
            val status = connection.responseCode
            val stream = if (status in 200..299) connection.inputStream else connection.errorStream
            val payload = stream?.let { readBounded(it) }.orEmpty()
            if (status !in 200..299) {
                throw IllegalStateException("DEEP33_AUTH_HTTP_$status")
            }
            val json = JSONObject(payload)
            val accessToken = json.optString("access_token").trim()
            val newRefreshToken = json.optString("refresh_token").trim()
            val userId = json.optString("user_id").trim()
            val memoryProfileId = json.optString("memory_profile_id", profileId).trim()
            val expiresAt = when {
                json.has("expires_at") && !json.isNull("expires_at") -> json.optLong("expires_at", 0L)
                json.optLong("expires_in", 0L) > 0L ->
                    (System.currentTimeMillis() / 1000L) + json.optLong("expires_in")
                else -> 0L
            }
            if (
                accessToken.isBlank() ||
                newRefreshToken.isBlank() ||
                userId.isBlank() ||
                memoryProfileId != profileId ||
                expiresAt <= 0L
            ) {
                throw IllegalStateException("DEEP33_AUTH_RESPONSE_INVALID")
            }
            return Deep33AuthSession(
                accessToken = accessToken,
                refreshToken = newRefreshToken,
                userId = userId,
                memoryProfileId = memoryProfileId,
                expiresAtEpochSeconds = expiresAt
            )
        } finally {
            connection.disconnect()
        }
    }

    private fun readBounded(input: java.io.InputStream): String {
        input.bufferedReader(StandardCharsets.UTF_8).use { reader ->
            val out = StringBuilder()
            val buffer = CharArray(2048)
            var total = 0
            while (true) {
                val n = reader.read(buffer)
                if (n < 0) break
                total += n
                if (total > MAX_RESPONSE_BYTES) throw IllegalStateException("DEEP33_AUTH_RESPONSE_TOO_LARGE")
                out.append(buffer, 0, n)
            }
            return out.toString()
        }
    }

    private fun getKey(): javax.crypto.SecretKey {
        val keyStore = java.security.KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        val existing = keyStore.getKey(KEY_ALIAS, null) as? javax.crypto.SecretKey
        if (existing != null) return existing

        val generator = javax.crypto.KeyGenerator.getInstance(
            KeyProperties.KEY_ALGORITHM_AES,
            "AndroidKeyStore"
        )
        generator.init(
            KeyGenParameterSpec.Builder(
                KEY_ALIAS,
                KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT
            )
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setRandomizedEncryptionRequired(true)
                .build()
        )
        return generator.generateKey()
    }

    private fun encrypt(plain: String): String {
        val cipher = javax.crypto.Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(javax.crypto.Cipher.ENCRYPT_MODE, getKey())
        val ciphertext = cipher.doFinal(plain.toByteArray(StandardCharsets.UTF_8))
        val iv = cipher.iv
        return Base64.getEncoder().encodeToString(iv) + "." +
            Base64.getEncoder().encodeToString(ciphertext)
    }

    private fun decrypt(encoded: String): String? = runCatching {
        val parts = encoded.split('.', limit = 2)
        require(parts.size == 2)
        val iv = Base64.getDecoder().decode(parts[0])
        val ciphertext = Base64.getDecoder().decode(parts[1])
        val cipher = javax.crypto.Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(
            javax.crypto.Cipher.DECRYPT_MODE,
            getKey(),
            javax.crypto.spec.GCMParameterSpec(128, iv)
        )
        String(cipher.doFinal(ciphertext), StandardCharsets.UTF_8)
    }.getOrNull()

    private fun loadEncrypted(context: Context, profileId: String): Deep33AuthSession? {
        val prefs = context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val raw = prefs.getString(KEY_PREFIX + profileId, null) ?: return null
        val json = decrypt(raw)?.let { runCatching { JSONObject(it) }.getOrNull() } ?: return null
        return runCatching {
            Deep33AuthSession(
                accessToken = json.getString("access_token"),
                refreshToken = json.getString("refresh_token"),
                userId = json.getString("user_id"),
                memoryProfileId = json.getString("memory_profile_id"),
                expiresAtEpochSeconds = json.getLong("expires_at")
            )
        }.getOrNull()
    }

    private fun saveEncrypted(context: Context, profileId: String, session: Deep33AuthSession) {
        val json = JSONObject()
            .put("access_token", session.accessToken)
            .put("refresh_token", session.refreshToken)
            .put("user_id", session.userId)
            .put("memory_profile_id", session.memoryProfileId)
            .put("expires_at", session.expiresAtEpochSeconds)
        context.applicationContext
            .getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit()
            .putString(KEY_PREFIX + profileId, encrypt(json.toString()))
            .apply()
    }

    fun forgetProfile(context: Context, profileId: String) {
        val id = profileId.trim()
        cache.remove(id)
        locks.remove(id)
        context.applicationContext
            .getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit()
            .remove(KEY_PREFIX + id)
            .apply()
    }
}
