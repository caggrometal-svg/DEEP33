package cl.caggrometal.deep33

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.Executors
import java.util.concurrent.ScheduledExecutorService
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicReference

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
    val profileId: String,
    val sessionId: String,
    val requestId: String,
    val idempotencyKey: String,
    val personality: String,
    val payloadJson: String,
    val conversationJson: String = "[]"
)

data class PendingMemorySync(
    val profileId: String,
    val sessionId: String,
    val requestId: String,
    val personality: String,
    val memoryProfileId: String,
    val messagesJson: String
)

enum class GenerationStatus { RUNNING, CANCELLING, DONE, FAILED, RETRYABLE, CANCELLED }

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
    name: String? = null,
    profileIdOverride: String? = null
) {
    private val appContext = context.applicationContext
    val profileId: String = profileIdOverride ?: MultiUserIdentity.ensureActiveProfileId(appContext)
    private val prefs = appContext.getSharedPreferences(
        name ?: PREFS_NAME + "_" + profileId,
        Context.MODE_PRIVATE
    )

    init {
        if (name == null && profileIdOverride == null) migrateLegacyInstallState(appContext)
        if (name == null) migrateGpsPrivacyState()
    }

    val memoryProfileId: String
        get() = profileId

    private fun sanitizeGpsText(value: String): String {
        return value
            .replace(
                Regex("\\[(?:UBICACIÓN|UBICACION) GPS ACTUAL[^\\]]*\\]", RegexOption.IGNORE_CASE),
                "[contexto local eliminado por privacidad]"
            )
            .replace(
                Regex("\\[(?:CONTEXTO) LOCAL ACTUAL[^\\]]*\\]", RegexOption.IGNORE_CASE),
                "[contexto local]"
            )
            .replace(
                Regex("(?i)\\b(?:latitude|longitude|lat|lon)\\b\\s*[=:]\\s*-?\\d+(?:\\.\\d+)?"),
                "[coordenada eliminada por privacidad]"
            )
            .replace(
                Regex("(?i)(\\\"(?:latitude|longitude)\\\"\\s*:\\s*)-?\\d+(?:\\.\\d+)?"),
                "$1null"
            )
    }
    }

    private fun migrateGpsPrivacyState() {
        if (prefs.getBoolean(KEY_GPS_PRIVACY_MIGRATED, false)) return
        synchronized(STORE_LOCK) {
            if (prefs.getBoolean(KEY_GPS_PRIVACY_MIGRATED, false)) return@synchronized
            val edit = prefs.edit()
            var changed = false
            prefs.all.forEach { (key, value) ->
                if (value is String) {
                    val sanitized = sanitizeGpsText(value)
                    if (sanitized != value) {
                        edit.putString(key, sanitized)
                        changed = true
                    }
                }
            }
            edit.putBoolean(KEY_GPS_PRIVACY_MIGRATED, true)
            if (changed) edit.commit() else edit.apply()
        }
    }

    private fun migrateLegacyInstallState(context: Context) {
        if (prefs.getBoolean(KEY_LEGACY_MIGRATED, false)) return
        val legacy = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        if (legacy.all.isEmpty()) {
            prefs.edit().putBoolean(KEY_LEGACY_MIGRATED, true).apply()
            return
        }
        synchronized(STORE_LOCK) {
            if (prefs.getBoolean(KEY_LEGACY_MIGRATED, false)) return@synchronized
            val edit = prefs.edit()
            legacy.all.forEach { (key, value) ->
                if (key == KEY_MEMORY_PROFILE_ID) return@forEach
                when (value) {
                    is String -> {
                        var migrated = value
                        if (key == KEY_PENDING_MEMORY_SYNC) {
                            runCatching {
                                migrated = JSONObject(value)
                                    .put("profile_id", profileId)
                                    .put("memory_profile_id", profileId)
                                    .toString()
                            }
                        } else if (key == KEY_PENDING_TURN) {
                            runCatching {
                                val pending = JSONObject(value).put("profile_id", profileId)
                                val payload = JSONArray(pending.optString("payload", "[]"))
                                val safePayload = JSONArray()
                                val safeConversation = JSONArray()
                                for (index in 0 until payload.length()) {
                                    val item = payload.optJSONObject(index) ?: continue
                                    val role = item.optString("role")
                                    val safeContent = sanitizeGpsText(item.optString("content"))
                                    safePayload.put(
                                        JSONObject()
                                            .put("role", role)
                                            .put("content", safeContent)
                                    )
                                    if (role == "user" || role == "assistant") {
                                        safeConversation.put(
                                            JSONObject()
                                                .put("role", role)
                                                .put("content", safeContent)
                                        )
                                    }
                                }
                                migrated = pending
                                    .put("payload", safePayload)
                                    .put("conversation", safeConversation)
                                    .toString()
                            }
                        }
                        edit.putString(key, migrated)
                    }
                    is Boolean -> edit.putBoolean(key, value)
                    is Int -> edit.putInt(key, value)
                    is Long -> edit.putLong(key, value)
                    is Float -> edit.putFloat(key, value)
                    is Set<*> -> edit.putStringSet(key, value.filterIsInstance<String>().toSet())
                }
            }
            edit.putBoolean(KEY_LEGACY_MIGRATED, true).commit()
        }
    }

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
            messages.forEach {
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
                .put("profile_id", turn.profileId)
                .put("session_id", turn.sessionId)
                .put("request_id", turn.requestId)
                .put("idempotency_key", turn.idempotencyKey)
                .put("personality", turn.personality)
                .put("payload", turn.payloadJson)
                .put("conversation", turn.conversationJson)
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
            val profileId = json.optString("profile_id", this@SessionStore.profileId)
            val personality = json.optString("personality", "NEUTRO")
            val payload = json.optString("payload")
            val conversation = json.optString("conversation", "[]")
            if (
                profileId != this@SessionStore.profileId ||
                session.isBlank() ||
                requestId.isBlank() ||
                idempotencyKey.isBlank() ||
                payload.isBlank()
            ) {
                null
            } else {
                JSONArray(payload)
                JSONArray(conversation)
                PendingTurn(profileId, session, requestId, idempotencyKey, personality, payload, conversation)
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
                        .put("profile_id", it.profileId)
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
                    val profileId = item.optString("profile_id", this@SessionStore.profileId)
                    val sessionId = item.optString("session_id")
                    val requestId = item.optString("request_id")
                    val personality = item.optString("personality", "NEUTRO")
                    val memoryProfileId = item.optString("memory_profile_id", profileId)
                    val messagesJson = item.optString("messages")
                    if (
                        profileId != this@SessionStore.profileId ||
                        sessionId.isBlank() ||
                        requestId.isBlank() ||
                        messagesJson.isBlank()
                    ) continue
                    runCatching { JSONArray(messagesJson) }.getOrNull() ?: continue
                    add(PendingMemorySync(profileId, sessionId, requestId, personality, memoryProfileId, messagesJson))
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
        cancelQueuedGenerationCheckpoint(requestId)
        val state = GenerationState(
            status = status,
            requestId = requestId,
            sessionId = sessionId,
            personality = personality,
            partialOutput = partialOutput,
            finalText = finalText,
            error = error
        )
        writeGenerationState(state, durable)
        generationStateListeners.forEach { listener ->
            runCatching { listener(state) }
        }
    }

    /**
     * Queue a RUNNING checkpoint without blocking the SSE receiver on disk I/O.
     * Only the latest checkpoint is persisted; listeners receive the state immediately.
     */
    fun queueGenerationCheckpoint(
        status: GenerationStatus,
        requestId: String,
        sessionId: String,
        personality: String,
        partialOutput: String = "",
        error: String = ""
    ) {
        val state = GenerationState(
            status = status,
            requestId = requestId,
            sessionId = sessionId,
            personality = personality,
            partialOutput = partialOutput,
            finalText = "",
            error = error
        )
        pendingGenerationCheckpoint.set(state)
        generationStateListeners.forEach { listener ->
            runCatching { listener(state) }
        }
        scheduleGenerationCheckpointFlush()
    }

    /**
     * Force the newest queued checkpoint to disk. Used only at service teardown,
     * outside the normal SSE callback path.
     */
    fun flushGenerationCheckpoint(requestId: String? = null) {
        val pending = pendingGenerationCheckpoint.get()
        if (pending != null && (requestId == null || pending.requestId == requestId)) {
            if (pendingGenerationCheckpoint.compareAndSet(pending, null)) {
                writeGenerationState(pending, durable = true)
            }
        }
        generationCheckpointFlushScheduled.set(false)
    }

    private fun scheduleGenerationCheckpointFlush() {
        if (!generationCheckpointFlushScheduled.compareAndSet(false, true)) return
        generationCheckpointExecutor.schedule({
            generationCheckpointFlushScheduled.set(false)
            val pending = pendingGenerationCheckpoint.getAndSet(null)
            if (pending != null) {
                writeGenerationState(pending, durable = false)
            }
            if (pendingGenerationCheckpoint.get() != null) {
                scheduleGenerationCheckpointFlush()
            }
        }, CHECKPOINT_FLUSH_DELAY_MS, TimeUnit.MILLISECONDS)
    }

    private fun cancelQueuedGenerationCheckpoint(requestId: String) {
        val pending = pendingGenerationCheckpoint.get()
        if (pending?.requestId == requestId) {
            pendingGenerationCheckpoint.compareAndSet(pending, null)
        }
    }

    private fun writeGenerationState(state: GenerationState, durable: Boolean) {
        synchronized(STORE_LOCK) {
            val json = JSONObject()
                .put("status", state.status.name)
                .put("request_id", state.requestId)
                .put("session_id", state.sessionId)
                .put("personality", state.personality)
                .put("partial_output", state.partialOutput)
                .put("final_text", state.finalText)
                .put("error", state.error)
            val edit = prefs.edit().putString(KEY_GENERATION_STATE, json.toString())
            if (durable) {
                // Terminal/recovery boundaries are synchronous by design.
                edit.commit()
            } else {
                edit.apply()
            }
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

    /**
     * Publish the cancellation transition atomically. The pending request is retained
     * until the generation service acknowledges terminal cancellation.
     */
    fun requestGenerationCancellation(
        requestId: String,
        sessionId: String,
        personality: String
    ): Boolean {
        if (requestId.isBlank() || sessionId.isBlank()) return false
        val state = GenerationState(
            status = GenerationStatus.CANCELLING,
            requestId = requestId,
            sessionId = sessionId,
            personality = personality,
            error = "Generación cancelando…"
        )
        synchronized(STORE_LOCK) {
            val current = loadGenerationState()
            if (current?.requestId == requestId &&
                current.status in setOf(GenerationStatus.CANCELLED, GenerationStatus.DONE, GenerationStatus.FAILED)
            ) return false
            if (current != null && current.requestId != requestId &&
                current.status in setOf(GenerationStatus.RUNNING, GenerationStatus.CANCELLING)
            ) return false
            cancelQueuedGenerationCheckpoint(requestId)
            writeGenerationState(state, durable = true)
        }
        generationStateListeners.forEach { listener -> runCatching { listener(state) } }
        return true
    }

    /**
     * Terminal cancellation boundary. This cannot overwrite DONE and removes the
     * pending replay marker in the same durable write.
     */
    fun finalizeGenerationCancellation(
        requestId: String,
        sessionId: String,
        personality: String
    ): Boolean {
        if (requestId.isBlank() || sessionId.isBlank()) return false
        val state = GenerationState(
            status = GenerationStatus.CANCELLED,
            requestId = requestId,
            sessionId = sessionId,
            personality = personality,
            error = "Generación cancelada."
        )
        synchronized(STORE_LOCK) {
            val current = loadGenerationState()
            if (current?.requestId == requestId && current.status == GenerationStatus.DONE) return false
            if (current != null && current.requestId != requestId) return false
            cancelQueuedGenerationCheckpoint(requestId)
            val json = JSONObject()
                .put("status", state.status.name)
                .put("request_id", state.requestId)
                .put("session_id", state.sessionId)
                .put("personality", state.personality)
                .put("partial_output", "")
                .put("final_text", "")
                .put("error", state.error)
            prefs.edit().putString(KEY_GENERATION_STATE, json.toString()).remove(KEY_PENDING_TURN).commit()
        }
        generationStateListeners.forEach { listener -> runCatching { listener(state) } }
        return true
    }

    /**
     * Complete a generation atomically. A cancellation that wins the race prevents
     * DONE from ever being published.
     */
    fun completeGeneration(
        requestId: String,
        sessionId: String,
        personality: String,
        messages: List<UiMessage>,
        finalText: String
    ): Boolean {
        if (requestId.isBlank() || sessionId.isBlank() || finalText.isBlank()) return false
        val state = GenerationState(
            status = GenerationStatus.DONE,
            requestId = requestId,
            sessionId = sessionId,
            personality = personality,
            partialOutput = finalText,
            finalText = finalText
        )
        synchronized(STORE_LOCK) {
            val current = loadGenerationState()
            if (current?.requestId == requestId &&
                current.status in setOf(GenerationStatus.CANCELLING, GenerationStatus.CANCELLED)
            ) return false
            if (current != null && current.requestId != requestId &&
                current.status in setOf(GenerationStatus.RUNNING, GenerationStatus.CANCELLING)
            ) return false
            cancelQueuedGenerationCheckpoint(requestId)
            val messagesJson = JSONArray()
            messages.takeLast(MAX_MESSAGES).forEach {
                messagesJson.put(JSONObject().put("role", it.role).put("content", it.content))
            }
            val stateJson = JSONObject()
                .put("status", state.status.name)
                .put("request_id", state.requestId)
                .put("session_id", state.sessionId)
                .put("personality", state.personality)
                .put("partial_output", state.partialOutput)
                .put("final_text", state.finalText)
                .put("error", "")
            prefs.edit()
                .putString(messagesKey(sessionId), messagesJson.toString())
                .putString(KEY_GENERATION_STATE, stateJson.toString())
                .remove(KEY_PENDING_TURN)
                .commit()
        }
        generationStateListeners.forEach { listener -> runCatching { listener(state) } }
        return true
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
            val start = 0
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
        private const val KEY_SESSION_ID = "session_id"
        private const val KEY_MEMORY_PROFILE_ID = "memory_profile_id"
        private const val KEY_LEGACY_MIGRATED = "legacy_migrated"
        private const val KEY_GPS_PRIVACY_MIGRATED = "gps_privacy_migrated"
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
        private const val MAX_CHAT_SUMMARIES = 30
        private const val MAX_PENDING_MEMORY_SYNCS = 10
        private val generationStateListeners = java.util.concurrent.CopyOnWriteArrayList<(GenerationState) -> Unit>()
        private const val CHECKPOINT_FLUSH_DELAY_MS = 750L
        private val generationCheckpointExecutor: ScheduledExecutorService =
            Executors.newSingleThreadScheduledExecutor { runnable ->
                Thread(runnable, "DEEP33-GenerationCheckpoint").apply { isDaemon = true }
            }
        private val generationCheckpointFlushScheduled = AtomicBoolean(false)
        private val pendingGenerationCheckpoint = AtomicReference<GenerationState?>(null)
    }
}
