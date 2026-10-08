package cl.caggrometal.deep33

import android.content.Context
import android.util.Base64
import java.net.URL
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec
import javax.net.ssl.HttpsURLConnection
import org.json.JSONObject

/**
 * Per-local-profile Supabase identity. Server/provider secrets never enter the APK.
 */
class SupabaseAuthManager(
    context: Context,
    private val profileId: String,
) {
    private val prefs = context.applicationContext.getSharedPreferences(
        "deep33_auth_" + profileId,
        Context.MODE_PRIVATE
    )

    companion object {
        private const val AUTH_BASE = BuildConfig.DEEP33_SUPABASE_URL + "/auth/v1"
        private const val AUTH_TIMEOUT_MS = 8_000
        private const val ACCESS_TOKEN = "access_token"
        private const val REFRESH_TOKEN = "refresh_token"
        private const val EXPIRES_AT = "expires_at"
        private const val USER_ID = "user_id"
        private const val TOKEN_SKEW_MS = 60_000L
        private val lock = Any()
    }

    fun storedUserId(): String? = prefs.getString(USER_ID, null)?.takeIf { it.isNotBlank() }

    fun ensureSession(): String? = synchronized(lock) {
        val existing = secret(ACCESS_TOKEN)
        val expiresAt = prefs.getLong(EXPIRES_AT, 0L)
        if (!existing.isNullOrBlank() && expiresAt > System.currentTimeMillis() + TOKEN_SKEW_MS) {
            return@synchronized existing
        }

        val refresh = secret(REFRESH_TOKEN)
        if (!refresh.isNullOrBlank() && refreshToken(refresh)) {
            secret(ACCESS_TOKEN)?.takeIf { it.isNotBlank() }?.let { return@synchronized it }
        }

        return@synchronized if (bootstrapSession()) secret(ACCESS_TOKEN) else null
    }

    private fun bootstrapSession(): Boolean {
        val body = JSONObject()
            .put("action", "session")
            .put("memory_profile_id", profileId)
        val json = post(
            BuildConfig.DEEP33_SUPABASE_URL + "/functions/v1/deep33-auth",
            body
        ) ?: return false
        return persistSession(json)
    }

    private fun refreshToken(refreshToken: String): Boolean {
        val json = post(
            "/token?grant_type=refresh_token",
            JSONObject().put("refresh_token", refreshToken)
        ) ?: return false
        return persistSession(json)
    }

    private fun persistSession(json: JSONObject): Boolean {
        val access = json.optString("access_token").trim()
        val refresh = json.optString("refresh_token").trim()
        val userId = (
            json.optString("user_id").trim().ifBlank {
                json.optJSONObject("user")?.optString("id")?.trim().orEmpty()
            }
        )
        if (access.isBlank() || refresh.isBlank() || userId.isBlank()) return false

        val expiresInSeconds = json.optLong("expires_in", 3_600L).coerceIn(60L, 86_400L)
        val absoluteExpiresAtSeconds = json.optLong("expires_at", 0L)
        val expiresAtMs = if (absoluteExpiresAtSeconds > 0L) {
            absoluteExpiresAtSeconds * 1000L
        } else {
            System.currentTimeMillis() + expiresInSeconds * 1000L
        }

        putSecret(ACCESS_TOKEN, access)
        putSecret(REFRESH_TOKEN, refresh)
        prefs.edit()
            .putLong(EXPIRES_AT, expiresAtMs)
            .putString(USER_ID, userId)
            .apply()
        return true
    }

    private fun post(path: String, body: JSONObject): JSONObject? {
        val connection = runCatching {
            (URL(AUTH_BASE + path).openConnection() as HttpsURLConnection).apply {
                requestMethod = "POST"
                connectTimeout = AUTH_TIMEOUT_MS
                readTimeout = AUTH_TIMEOUT_MS
                useCaches = false
                doInput = true
                doOutput = true
                instanceFollowRedirects = false
                setRequestProperty("Accept", "application/json")
                setRequestProperty("Content-Type", "application/json")
                setRequestProperty("apikey", BuildConfig.DEEP33_SUPABASE_PUBLISHABLE_KEY)
                outputStream.use { it.write(body.toString().toByteArray(Charsets.UTF_8)) }
            }
        }.getOrNull() ?: return null

        return try {
            if (connection.responseCode !in 200..299) return null
            JSONObject(connection.inputStream.bufferedReader(Charsets.UTF_8).use { it.readText() })
        } catch (_: Exception) {
            null
        } finally {
            connection.disconnect()
        }
    }

    private fun keystoreKey(): SecretKey {
        val alias = "DEEP33_AUTH_" + profileId.replace("[^A-Za-z0-9_-]".toRegex(), "_")
        val keyStore = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        val existing = keyStore.getKey(alias, null)
        if (existing is SecretKey) return existing

        val generator = KeyGenerator.getInstance("AES", "AndroidKeyStore")
        generator.init(
            android.security.keystore.KeyGenParameterSpec.Builder(
                alias,
                android.security.keystore.KeyProperties.PURPOSE_ENCRYPT or
                    android.security.keystore.KeyProperties.PURPOSE_DECRYPT,
            )
                .setBlockModes(android.security.keystore.KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(android.security.keystore.KeyProperties.ENCRYPTION_PADDING_NONE)
                .setRandomizedEncryptionRequired(true)
                .build()
        )
        return generator.generateKey()
    }

    private fun putSecret(key: String, value: String) {
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, keystoreKey())
        val encryptedPayload = cipher.doFinal(value.toByteArray(Charsets.UTF_8))
        val packed = ByteArray(cipher.iv.size + encryptedPayload.size)
        System.arraycopy(cipher.iv, 0, packed, 0, cipher.iv.size)
        System.arraycopy(encryptedPayload, 0, packed, cipher.iv.size, encryptedPayload.size)
        prefs.edit().putString(
            "secret_" + key,
            Base64.encodeToString(packed, Base64.NO_WRAP)
        ).apply()
    }

    private fun secret(key: String): String? {
        val encoded = prefs.getString("secret_" + key, null) ?: return null
        return runCatching {
            val bytes = Base64.decode(encoded, Base64.NO_WRAP)
            require(bytes.size > 12)
            val iv = bytes.copyOfRange(0, 12)
            val encryptedPayload = bytes.copyOfRange(12, bytes.size)
            val cipher = Cipher.getInstance("AES/GCM/NoPadding")
            cipher.init(Cipher.DECRYPT_MODE, keystoreKey(), GCMParameterSpec(128, iv))
            String(cipher.doFinal(encryptedPayload), Charsets.UTF_8)
        }.getOrNull()
    }
}
