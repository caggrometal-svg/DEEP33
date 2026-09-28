package cl.caggrometal.deep33

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject
import java.util.UUID

data class UiMessage(
    val role: String,
    val content: String
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
            prefs.edit().putString(KEY_SESSION_ID, created).apply()
            return created
        }

    fun resetSession() {
        prefs.edit()
            .putString(KEY_SESSION_ID, UUID.randomUUID().toString())
            .remove(KEY_MESSAGES)
            .apply()
    }

    fun clearConversation() {
        prefs.edit().remove(KEY_MESSAGES).apply()
    }

    fun loadMessages(): List<UiMessage> {
        val raw = prefs.getString(KEY_MESSAGES, null) ?: return emptyList()
        return try {
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
    }

    fun saveMessages(messages: List<UiMessage>) {
        val json = JSONArray()
        messages.takeLast(MAX_MESSAGES).forEach {
            json.put(JSONObject().put("role", it.role).put("content", it.content))
        }
        prefs.edit().putString(KEY_MESSAGES, json.toString()).apply()
    }

    companion object {
        private const val PREFS_NAME = "deep33_session"
        private const val KEY_SESSION_ID = "session_id"
        private const val KEY_MESSAGES = "messages_json"
        private const val MAX_MESSAGES = 50
    }
}
