package cl.caggrometal.deep33

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject

data class UiMessage(
    val role: String,
    val content: String
)

data class ChatSummary(
    val sessionId: String,
    val title: String,
    val updatedAt: Long
)

data class PendingTurn(
    val sessionId: String,
    val requestId: String,
    val idempotencyKey: String,
    val personality: String,
    val payloadJson: String,
    val memoryProfileId: String
)

data class UserProfile(
    val id: String,
    val name: String
)

data class PendingMemorySync(
    val sessionId: String,
    val requestId: String,
    val personality: String,
    val memoryProfileId: String,
    val messagesJson: String
)

enum class GenerationStatus { RUNNING, DONE, FAILED, RETRYABLE, CANCELLED }

data class GenerationState(
    val status: GenerationStatus,
    val requestId: String,
    val sessionId: String,
    val personality: String,
    val partialOutput: String = "",
    val finalText: String = "",
    val error: String = ""
)

class SessionStore(
    context: Context,
    name: String = PREFS_NAME,
    profileIdOverride: String? = null
) {
    private val appContext = context.applicationContext
    private val profileRegistry =
        appContext.getSharedPreferences(PROFILE_REGISTRY_PREFS, Context.MODE_PRIVATE)
    private val profileMode = name == PREFS_NAME
    private val selectedProfileId =
        if (profileMode) resolveProfileId(profileIdOverride) else (profileIdOverride ?: "test")
    private val prefs = if (profileMode) {
        appContext.getSharedPreferences(profilePrefsName(selectedProfileId), Context.MODE_PRIVATE)
    } else {
        appContext.getSharedPreferences(name, Context.MODE_PRIVATE)
    }

    init {
        if (profileMode) migrateLegacyInstallationStore(selectedProfileId)
    }

    val profileId: String
        get() = selectedProfileId

    val memoryProfileId: String
        get() = selectedProfileId

    val activeProfileName: String
        get() = synchronized(STORE_LOCK) {
            loadUserProfiles().firstOrNull { it.id == selectedProfileId }?.name ?: "Usuario"
        }

    fun listUserProfiles(): List<UserProfile> =
        synchronized(STORE_LOCK) { loadUserProfiles() }

    fun createUserProfile(displayName: String? = null): UserProfile {
        synchronized(STORE_LOCK) {
            val existing = loadUserProfiles()
            val id = MultiUserIdentity.newProfileId()
            val clean = displayName?.trim()?.replace(Regex("\\s+"), " ").orEmpty()
            val name = clean.take(40).ifBlank { "Usuario " + (existing.size + 1) }
            val created = UserProfile(id, name)
            saveUserProfiles(existing + created)
            return created
        }
    }

    fun activateUserProfile(id: String): Boolean {
        val normalized = id.trim()
        if (!normalized.matches(Regex(PROFILE_ID_PATTERN))) return false
        synchronized(STORE_LOCK) {
            if (loadUserProfiles().none { it.id == normalized }) return false
            profileRegistry.edit().putString(KEY_ACTIVE_PROFILE_ID, normalized).commit()
            return true
        }
    }

    private fun resolveProfileId(override: String?): String {
        val requested = override?.trim().orEmpty()
        if (requested.isNotBlank()) {
            require(requested.matches(Regex(PROFILE_ID_PATTERN))) { "Invalid DEEP33 profile id" }
            ensureProfileRegistered(requested, null)
            return requested
        }

        val active = profileRegistry.getString(KEY_ACTIVE_PROFILE_ID, null)?.trim().orEmpty()
        if (active.matches(Regex(PROFILE_ID_PATTERN))) {
            ensureProfileRegistered(active, null)
            return active
        }

        val legacy = appContext.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        val legacyId = legacy.getString(KEY_MEMORY_PROFILE_ID, null)?.trim().orEmpty()
        val id = if (legacyId.matches(Regex(PROFILE_ID_PATTERN))) legacyId else MultiUserIdentity.newProfileId()
        ensureProfileRegistered(id, if (legacyId.isNotBlank()) "Usuario 1" else null)
        profileRegistry.edit().putString(KEY_ACTIVE_PROFILE_ID, id).commit()
        return id
    }

    private fun ensureProfileRegistered(id: String, defaultName: String?) {
        val profiles = loadUserProfiles()
        if (profiles.any { it.id == id }) return
        saveUserProfiles(profiles + UserProfile(id, defaultName ?: "Usuario " + (profiles.size + 1)))
    }

    private fun loadUserProfiles(): List<UserProfile> {
        val raw = profileRegistry.getString(KEY_PROFILES_JSON, null) ?: return emptyList()
        return runCatching {
            val json = JSONArray(raw)
            buildList {
                for (i in 0 until json.length()) {
                    val item = json.optJSONObject(i) ?: continue
                    val id = item.optString("id").trim()
                    val name = item.optString("name").trim()
                    if (id.matches(Regex(PROFILE_ID_PATTERN)) && name.isNotBlank()) {
                        add(UserProfile(id, name.take(40)))
                    }
                }
            }
        }.getOrDefault(emptyList())
    }

    private fun saveUserProfiles(profiles: List<UserProfile>) {
        val json = JSONArray()
        profiles.distinctBy { it.id }.take(MAX_USER_PROFILES).forEach {
            json.put(JSONObject().put("id", it.id).put("name", it.name))
        }
        profileRegistry.edit().putString(KEY_PROFILES_JSON, json.toString()).apply()
    }

    private fun migrateLegacyInstallationStore(profileId: String) {
        synchronized(STORE_LOCK) {
            if (prefs.all.isNotEmpty()) return
            val legacy = appContext.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            val values = legacy.all
            if (values.isEmpty()) return
            val edit = prefs.edit()
            values.forEach { (key, value) ->
                when (value) {
                    is String -> edit.putString(key, value)
                    is Boolean -> edit.putBoolean(key, value)
                    is Int -> edit.putInt(key, value)
                    is Long -> edit.putLong(key, value)
                    is Float -> edit.putFloat(key, value)
                    is Set<*> -> edit.putStringSet(key, value.filterIsInstance<String>().toSet())
                }
            }
            edit.putString(KEY_MEMORY_PROFILE_ID, profileId).commit()
        }
    }

    private fun profilePrefsName(id: String): String = "deep33_profile_$id"

    val sessionId: String
        get() = synchronized(STORE_LOCK) {
            val existing = prefs.getString(KEY_SESSION_ID, null)
            if (!existing.isNullOrBlank()) return@synchronized existing
            val created = MultiUserIdentity.newSessionId()
            prefs.edit()
                .putString(KEY_SESSION_ID, created)
                .putString(messagesKey(created), "[]")
                .commit()
            created
        }

    fun resetSession() = synchronized(STORE_LOCK) {
        val created = MultiUserIdentity.newSessionId()
        // Starting a fresh conversation must also invalidate any generation recovery
        // markers belonging to the previous conversation. Otherwise Activity startup
        // can consume an old DONE/RUNNING state before seeing the new pending turn.
        prefs.edit()
            .putString(KEY_SESSION_ID, created)
            .putString(messagesKey(created), "[]")
            .remove(KEY_PENDING_TURN)
            .remove(KEY_GENERATION_STATE)
            .commit()
    }

    fun activateSession(id: String) {
        if (id.isBlank()) return
        synchronized(STORE_LOCK) {
            val key = messagesKey(id)
            val edit = prefs.edit()
            if (!prefs.contains(key)) edit.putString(key, "[]")
            edit.putString(KEY_SESSION_ID, id).commit()
        }
    }

    fun clearConversation() {
        synchronized(STORE_LOCK) {
            prefs.edit()
                .putString(messagesKey(sessionId), "[]")
                .remove(KEY_MESSAGES_LEGACY)
                .commit()
        }
    }

    fun isGenerationCancelled(requestId: String): Boolean =
        synchronized(STORE_LOCK) {
            val state = loadGenerationState()
            state?.requestId == requestId && state.status == GenerationStatus.CANCELLED
        }

    fun cancelGenerationAtomically(
        requestId: String?,
        sessionId: String,
        personality: String,
    ): Boolean {
        val id = requestId?.trim().orEmpty()
        if (id.isBlank() || sessionId.isBlank()) return false
        synchronized(STORE_LOCK) {
            val current = loadGenerationState()
            val pending = loadPendingTurn()
            val matches = current?.requestId == id || pending?.requestId == id
            if (!matches) return false

            val edit = prefs.edit()
                .putString(
                    KEY_GENERATION_STATE,
                    JSONObject()
                        .put("status", GenerationStatus.CANCELLED.name)
                        .put("request_id", id)
                        .put("session_id", sessionId)
                        .put("personality", personality)
                        .put("partial_output", current?.partialOutput.orEmpty())
                        .put("final_text", "")
                        .put("error", "Generación cancelada.")
                        .toString()
                )
                .remove(KEY_PENDING_TURN)
            edit.commit()

            generationStateListeners.forEach { listener ->
                runCatching {
                    listener(
                        GenerationState(
                            status = GenerationStatus.CANCELLED,
                            requestId = id,
                            sessionId = sessionId,
                            personality = personality,
                            partialOutput = current?.partialOutput.orEmpty(),
                            error = "Generación cancelada."
                        )
                    )
                }
            }
            return true
        }
    }

    var appBackgrounded: Boolean
        get() = prefs.getBoolean(KEY_APP_BACKGROUNDED, false)
        set(value) {
            // Lifecycle state must survive Activity recreation and service recreation.
            prefs.edit().putBoolean(KEY_APP_BACKGROUNDED, value).commit()
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

    val hasUserSelectedPersonality: Boolean
        get() = prefs.getBoolean(KEY_PERSONALITY_USER_SELECTED, false)

    fun setPersonalityFromUser(value: String) {
        prefs.edit()
            .putString(KEY_PERSONALITY, value)
            .putBoolean(KEY_PERSONALITY_USER_SELECTED, true)
            .apply()
    }

    fun applyRemotePersonalityIfUnset(value: String): Boolean {
        if (hasUserSelectedPersonality || value.isBlank()) return false
        prefs.edit().putString(KEY_PERSONALITY, value).apply()
        return true
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

    fun saveMessages(messages: List<UiMessage>, durable: Boolean = false) {
        synchronized(STORE_LOCK) {
            val json = JSONArray()
            messages.takeLast(MAX_MESSAGES).forEach {
                json.put(JSONObject().put("role", it.role).put("content", it.content))
            }
            val edit = prefs.edit().putString(messagesKey(sessionId), json.toString())
            if (durable) {
                // Final conversation data must reach persistent storage before a recoverable
                // generation marker is cleared after Activity/process destruction.
                edit.commit()
            } else {
                edit.apply()
            }
        }
    }

    fun savePendingTurn(turn: PendingTurn) {
        synchronized(STORE_LOCK) {
            val json = JSONObject()
                .put("session_id", turn.sessionId)
                .put("request_id", turn.requestId)
                .put("idempotency_key", turn.idempotencyKey)
                .put("personality", turn.personality)
                .put("payload", turn.payloadJson)
                .put("memory_profile_id", turn.memoryProfileId)
            // commit() is intentional: the pending turn must survive Activity destruction
            // before the network request begins.
            prefs.edit().putString(KEY_PENDING_TURN, json.toString()).commit()
        }
    }

    fun loadPendingTurn(): PendingTurn? {
        val raw = prefs.getString(KEY_PENDING_TURN, null) ?: return null
        return try {
            val json = JSONObject(raw)
            val session = json.optString("session_id")
            val requestId = json.optString("request_id")
            val idempotencyKey = json.optString("idempotency_key")
            val personality = json.optString("personality", "NEUTRO")
            val payload = json.optString("payload")
            val memoryProfileId = json.optString("memory_profile_id", this.memoryProfileId)
            if (
                session.isBlank() ||
                requestId.isBlank() ||
                idempotencyKey.isBlank() ||
                payload.isBlank() ||
                !memoryProfileId.matches(Regex(PROFILE_ID_PATTERN))
            ) {
                null
            } else {
                JSONArray(payload)
                PendingTurn(session, requestId, idempotencyKey, personality, payload, memoryProfileId)
            }
        } catch (_: Exception) {
            null
        }
    }

    fun clearPendingTurn(requestId: String? = null) {
        synchronized(STORE_LOCK) {
            val current = loadPendingTurn()
            if (requestId == null || current?.requestId == requestId) {
                prefs.edit().remove(KEY_PENDING_TURN).commit()
            }
        }
    }

    fun savePendingMemorySync(sync: PendingMemorySync) {
        synchronized(STORE_LOCK) {
            val queued = loadPendingMemorySyncQueue().toMutableList()
            queued.removeAll { it.requestId == sync.requestId }
            queued.add(sync)
            val json = JSONArray()
            queued.take(MAX_PENDING_MEMORY_SYNCS).forEach {
                json.put(
                    JSONObject()
                        .put("session_id", it.sessionId)
                        .put("request_id", it.requestId)
                        .put("personality", it.personality)
                        .put("memory_profile_id", it.memoryProfileId)
                        .put("messages", it.messagesJson)
                )
            }
            // Remote-memory retry state is a durable queue. Multiple completed chats may
            // finish while the backend is unavailable, so one marker must never overwrite
            // another session's unsent memory.
            prefs.edit().putString(KEY_PENDING_MEMORY_SYNC, json.toString()).commit()
        }
    }

    fun loadPendingMemorySync(): PendingMemorySync? =
        loadPendingMemorySyncQueue().firstOrNull()

    private fun loadPendingMemorySyncQueue(): List<PendingMemorySync> {
        val raw = prefs.getString(KEY_PENDING_MEMORY_SYNC, null) ?: return emptyList()
        return try {
            val json = JSONArray(raw)
            buildList {
                for (i in 0 until json.length()) {
                    val item = json.optJSONObject(i) ?: continue
                    val sessionId = item.optString("session_id")
                    val requestId = item.optString("request_id")
                    val personality = item.optString("personality", "NEUTRO")
                    val memoryProfileId = item.optString("memory_profile_id")
                    val messagesJson = item.optString("messages")
                    if (sessionId.isBlank() || requestId.isBlank() || memoryProfileId.isBlank() || messagesJson.isBlank()) continue
                    runCatching { JSONArray(messagesJson) }.getOrNull() ?: continue
                    add(PendingMemorySync(sessionId, requestId, personality, memoryProfileId, messagesJson))
                }
            }
        } catch (_: Exception) {
            emptyList()
        }
    }

    fun clearPendingMemorySync(requestId: String? = null) {
        synchronized(STORE_LOCK) {
            val queued = loadPendingMemorySyncQueue()
            val remaining = if (requestId == null) {
                emptyList()
            } else {
                queued.filterNot { it.requestId == requestId }
            }
            if (remaining.isEmpty()) {
                prefs.edit().remove(KEY_PENDING_MEMORY_SYNC).commit()
                return
            }
            val json = JSONArray()
            remaining.take(MAX_PENDING_MEMORY_SYNCS).forEach {
                json.put(
                    JSONObject()
                        .put("session_id", it.sessionId)
                        .put("request_id", it.requestId)
                        .put("personality", it.personality)
                        .put("memory_profile_id", it.memoryProfileId)
                        .put("messages", it.messagesJson)
                )
            }
            prefs.edit().putString(KEY_PENDING_MEMORY_SYNC, json.toString()).commit()
        }
    }

    fun saveGenerationState(
        status: GenerationStatus,
        requestId: String,
        sessionId: String,
        personality: String,
        partialOutput: String = "",
        finalText: String = "",
        error: String = "",
        durable: Boolean = false
    ) {
        val state = GenerationState(
            status = status,
            requestId = requestId,
            sessionId = sessionId,
            personality = personality,
            partialOutput = partialOutput,
            finalText = finalText,
            error = error
        )
        synchronized(STORE_LOCK) {
            val json = JSONObject()
                .put("status", status.name)
                .put("request_id", requestId)
                .put("session_id", sessionId)
                .put("personality", personality)
                .put("partial_output", partialOutput)
                .put("final_text", finalText)
                .put("error", error)
            val edit = prefs.edit().putString(KEY_GENERATION_STATE, json.toString())
            if (durable) {
                // DONE/FAILED/CANCELLED are recovery boundaries; commit them synchronously.
                edit.commit()
            } else {
                edit.apply()
            }
        }
        generationStateListeners.forEach { listener ->
            runCatching { listener(state) }
        }
    }

    fun addGenerationStateListener(listener: (GenerationState) -> Unit) {
        generationStateListeners.add(listener)
    }

    fun removeGenerationStateListener(listener: (GenerationState) -> Unit) {
        generationStateListeners.remove(listener)
    }

    fun loadGenerationState(): GenerationState? {
        val raw = prefs.getString(KEY_GENERATION_STATE, null) ?: return null
        return try {
            val json = JSONObject(raw)
            val status = GenerationStatus.valueOf(json.optString("status"))
            val requestId = json.optString("request_id")
            val sessionId = json.optString("session_id")
            val personality = json.optString("personality", "NEUTRO")
            if (requestId.isBlank() || sessionId.isBlank()) null
            else GenerationState(
                status = status,
                requestId = requestId,
                sessionId = sessionId,
                personality = personality,
                partialOutput = json.optString("partial_output"),
                finalText = json.optString("final_text"),
                error = json.optString("error")
            )
        } catch (_: Exception) {
            null
        }
    }

    fun clearGenerationState(requestId: String? = null) {
        synchronized(STORE_LOCK) {
            val current = loadGenerationState()
            if (requestId == null || current?.requestId == requestId) {
                prefs.edit().remove(KEY_GENERATION_STATE).commit()
            }
        }
    }

    /**
     * A DONE generation is a durable recovery boundary. Remove both markers together so
     * Activity/process recreation cannot replay a request whose final messages are already safe.
     */
    fun clearCompletedGeneration(requestId: String) {
        synchronized(STORE_LOCK) {
            val state = loadGenerationState()
            val pending = loadPendingTurn()
            val edit = prefs.edit()
            if (state?.status == GenerationStatus.DONE && state.requestId == requestId) {
                edit.remove(KEY_GENERATION_STATE)
            }
            if (pending?.requestId == requestId) {
                edit.remove(KEY_PENDING_TURN)
            }
            edit.commit()
        }
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
        // Shared by every SessionStore instance in the process. SharedPreferences is
        // thread-safe for individual operations, but generation recovery relies on
        // read-modify-write transactions that must be atomic across Activity and Service.
        private val STORE_LOCK = Any()
        private const val PREFS_NAME = "deep33_session"
        private const val PROFILE_REGISTRY_PREFS = "deep33_user_profiles"
        private const val KEY_ACTIVE_PROFILE_ID = "active_profile_id"
        private const val KEY_PROFILES_JSON = "profiles_json"
        private const val PROFILE_ID_PATTERN = "[A-Za-z0-9._-]{8,96}"
        private const val MAX_USER_PROFILES = 12
        private const val KEY_SESSION_ID = "session_id"
        private const val KEY_MEMORY_PROFILE_ID = "memory_profile_id"
        private const val KEY_MESSAGES_LEGACY = "messages_json"
        private const val KEY_APP_BACKGROUNDED = "app_backgrounded"
        private const val KEY_VOICE_ENABLED = "voice_enabled"
        private const val KEY_PERSONALITY = "personality"
        private const val KEY_PERSONALITY_USER_SELECTED = "personality_user_selected"
        private const val KEY_VOICE_TONE = "voice_tone"
        private const val KEY_CHAT_SUMMARIES = "chat_summaries_json"
        private const val KEY_PENDING_TURN = "pending_turn_json"
        private const val KEY_PENDING_MEMORY_SYNC = "pending_memory_sync_json"
        private const val KEY_GENERATION_STATE = "generation_state_json"
        private const val MAX_MESSAGES = 50
        private const val MAX_CHAT_SUMMARIES = 30
        private const val MAX_PENDING_MEMORY_SYNCS = 10
        private val generationStateListeners = java.util.concurrent.CopyOnWriteArrayList<(GenerationState) -> Unit>()
    }
}
