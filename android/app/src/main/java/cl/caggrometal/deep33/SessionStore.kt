package cl.caggrometal.deep33

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject
import java.util.UUID

data class UiMessage(
    val role: String,
    val content: String
)

data class ChatSummary(
    val sessionId: String,
    val title: String,
    val updatedAt: Long
)

class SessionStore(
    context: Context,
    name: String = PREFS_NAME
) {
    private val prefs = context.getSharedPreferences(name, Context.MODE_PRIVATE)

    val sessionId: String
        get() {
            val existing = prefs.getString(KEY_SESSION_ID, null)
            if (!existing.isNullOrBlank()) return existing
            val created = UUID.randomUUID().toString()
            prefs.edit()
                .putString(KEY_SESSION_ID, created)
                .putString(messagesKey(created), "[]")
                .apply()
            return created
        }

    fun resetSession() {
        val created = UUID.randomUUID().toString()
        prefs.edit()
            .putString(KEY_SESSION_ID, created)
            .putString(messagesKey(created), "[]")
            .apply()
    }

    fun activateSession(id: String) {
        if (id.isBlank()) return
        val key = messagesKey(id)
        if (!prefs.contains(key)) prefs.edit().putString(key, "[]").apply()
        prefs.edit().putString(KEY_SESSION_ID, id).apply()
    }

    fun clearConversation() {
        prefs.edit()
            .putString(messagesKey(sessionId), "[]")
            .remove(KEY_MESSAGES_LEGACY)
            .apply()
    }

    var voiceEnabled: Boolean
        get() = prefs.getBoolean(KEY_VOICE_ENABLED, false)
        set(value) {
            prefs.edit().putBoolean(KEY_VOICE_ENABLED, value).apply()
        }

    var personality: String
        get() = prefs.getString(KEY_PERSONALITY, "NEUTRO") ?: "NEUTRO"
        set(value) {
            prefs.edit().putString(KEY_PERSONALITY, value).apply()
        }

    var voiceTone: String
        get() = prefs.getString(KEY_VOICE_TONE, "EMBER") ?: "EMBER"
        set(value) {
            prefs.edit().putString(KEY_VOICE_TONE, value).apply()
        }

    fun migrateNaturalVoiceDefault() {
        if (!prefs.contains(KEY_VOICE_TONE)) {
            prefs.edit().putString(KEY_VOICE_TONE, "EMBER").apply()
        }
    }

    fun loadMessages(): List<UiMessage> {
        val raw = prefs.getString(messagesKey(sessionId), null)
            ?: prefs.getString(KEY_MESSAGES_LEGACY, null)
            ?: return emptyList()
        return parseMessages(raw)
    }

    fun saveMessages(messages: List<UiMessage>) {
        val json = JSONArray()
        messages.takeLast(MAX_MESSAGES).forEach {
            json.put(JSONObject().put("role", it.role).put("content", it.content))
        }
        prefs.edit().putString(messagesKey(sessionId), json.toString()).apply()
    }

    fun saveChatSummary(title: String) {
        val normalized = title.replace(Regex("\\s+"), " ").trim().take(60)
        if (normalized.isBlank()) return
        val current = loadChatSummaries().toMutableList()
        current.removeAll { it.sessionId == sessionId }
        current.add(ChatSummary(sessionId, normalized, System.currentTimeMillis()))
        current.sortByDescending { it.updatedAt }

        val json = JSONArray()
        current.take(MAX_CHAT_SUMMARIES).forEach {
            json.put(
                JSONObject()
                    .put("session_id", it.sessionId)
                    .put("title", it.title)
                    .put("updated_at", it.updatedAt)
            )
        }
        prefs.edit().putString(KEY_CHAT_SUMMARIES, json.toString()).apply()
    }

    fun loadChatSummaries(): List<ChatSummary> {
        val raw = prefs.getString(KEY_CHAT_SUMMARIES, null) ?: return emptyList()
        return try {
            val json = JSONArray(raw)
            buildList {
                for (i in 0 until json.length()) {
                    val item = json.optJSONObject(i) ?: continue
                    val id = item.optString("session_id")
                    val title = item.optString("title")
                    val updated = item.optLong("updated_at", 0L)
                    if (id.isNotBlank() && title.isNotBlank()) {
                        add(ChatSummary(id, title, updated))
                    }
                }
            }.sortedByDescending { it.updatedAt }
        } catch (_: Exception) {
            emptyList()
        }
    }

    private fun parseMessages(raw: String): List<UiMessage> = try {
        val json = JSONArray(raw)
        buildList {
            val start = maxOf(0, json.length() - MAX_MESSAGES)
            for (i in start until json.length()) {
                val item = json.optJSONObject(i) ?: continue
                val role = item.optString("role")
                val content = item.optString("content")
                if (role.isNotBlank() && content.isNotBlank()) add(UiMessage(role, content))
            }
        }
    } catch (_: Exception) {
        emptyList()
    }

    private fun messagesKey(id: String): String = "messages_$id"

    companion object {
        private const val PREFS_NAME = "deep33_session"
        private const val KEY_SESSION_ID = "session_id"
        private const val KEY_MESSAGES_LEGACY = "messages_json"
        private const val KEY_VOICE_ENABLED = "voice_enabled"
        private const val KEY_PERSONALITY = "personality"
        private const val KEY_VOICE_TONE = "voice_tone"
        private const val KEY_CHAT_SUMMARIES = "chat_summaries_json"
        private const val MAX_MESSAGES = 50
        private const val MAX_CHAT_SUMMARIES = 30
    }
}
