package cl.caggrometal.deep33

import android.Manifest
import android.media.AudioAttributes
import android.app.Activity
import android.app.AlertDialog
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.os.Bundle
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.os.Handler
import android.os.Looper
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import android.text.method.LinkMovementMethod
import android.util.Log
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.EditText
import android.widget.ImageButton
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import org.json.JSONObject
import java.util.Locale
import java.security.MessageDigest
import java.util.UUID
import java.util.concurrent.Callable
import java.util.concurrent.Executors
import java.util.concurrent.RejectedExecutionException
import java.util.concurrent.TimeUnit
import kotlin.math.roundToInt

private enum class ConnectionState { CONNECTING, ONLINE, OFFLINE }

private object Deep33Theme {
    val BG = Color.rgb(3, 3, 3)
    val SURFACE = Color.rgb(8, 8, 8)
    val SURFACE_2 = Color.rgb(13, 13, 13)
    val SURFACE_3 = Color.rgb(18, 18, 18)
    val LINE = Color.rgb(29, 29, 29)
    val LINE_SOFT = Color.rgb(38, 38, 38)
    val TEXT = Color.rgb(244, 244, 244)
    val TEXT_MUTED = Color.rgb(150, 150, 150)
    val RED = Color.rgb(255, 23, 68)
    val RED_DEEP = Color.rgb(102, 7, 24)
    val RED_SURFACE = Color.rgb(28, 7, 12)
    val USER_BUBBLE = Color.rgb(16, 16, 16)
    val ASSISTANT_BUBBLE = Color.rgb(10, 10, 10)
}

private object VoiceConversationPolicy {
    const val MAX_SPOKEN_SENTENCES = 3
    const val MAX_SPOKEN_CHARS = 420

    fun compactForSpeech(text: String): String {
        val cleaned = text
            .replace(Regex("\\[([^]]+)\\]\\(([^)]+)\\)"), "$1")
            .replace(Regex("[*_#>]"), "")
            .replace("`", "")
            .replace(Regex("\\s+"), " ")
            .trim()
        if (cleaned.length <= MAX_SPOKEN_CHARS) return cleaned

        val sentences = cleaned
            .split(Regex("(?<=[.!?])\\s+"))
            .filter { it.isNotBlank() }
        val limited = sentences.take(MAX_SPOKEN_SENTENCES).joinToString(" ").trim()
        val candidate = if (limited.isNotBlank()) limited else cleaned
        if (candidate.length <= MAX_SPOKEN_CHARS) return candidate

        val cut = candidate
            .take(MAX_SPOKEN_CHARS)
            .substringBeforeLast(' ')
            .trimEnd()
        return (if (cut.length >= 120) cut else candidate.take(MAX_SPOKEN_CHARS).trimEnd()) + "…"
    }

    fun normalizeForComparison(value: String): String =
        java.text.Normalizer
            .normalize(value.lowercase(Locale.ROOT), java.text.Normalizer.Form.NFD)
            .replace(Regex("\\p{M}+"), "")
            .replace(Regex("[^a-z0-9ñ\\s]"), " ")
            .replace(Regex("\\s+"), " ")
            .trim()

    fun isStopCommand(value: String): Boolean {
        val normalized = normalizeForComparison(value)
        if (normalized.isBlank()) return false
        val commands = listOf(
            "para",
            "parar",
            "detenlo",
            "detener",
            "deja de hablar",
            "para de hablar",
            "detente",
            "deten",
            "corta",
            "basta",
            "callate",
            "silencio",
            "espera",
            "un segundo",
            "espera un segundo",
            "ya basta"
        )
        return commands.any {
            normalized == it || normalized.startsWith("$it ") || normalized.contains(" $it ")
        }
    }

    fun looksLikeTtsEcho(partial: String, spoken: String): Boolean {
        val p = normalizeForComparison(partial)
        val s = normalizeForComparison(spoken)
        if (p.length < 3 || s.isBlank()) return false
        return s.startsWith(p)
    }
}

// Build trigger: package the current voice-command reliability fixes.
class MainActivity : Activity() {
    private lateinit var rootFrame: FrameLayout
    private lateinit var contentFrame: FrameLayout
    private lateinit var sidebar: LinearLayout
    private lateinit var drawerScrim: View
    private lateinit var historyContainer: LinearLayout
    private lateinit var chatContainer: LinearLayout
    private lateinit var input: EditText
    private lateinit var sendButton: ImageButton
    private lateinit var micButton: ImageButton
    private lateinit var cancelButton: ImageButton
    private lateinit var statusView: TextView
    private lateinit var currentPersonalityView: TextView
    private lateinit var currentProfileView: TextView
    private lateinit var diagnosticsView: TextView
    private lateinit var voicePanel: LinearLayout
    private lateinit var voiceStateView: TextView
    private lateinit var avatarView: VoiceAvatarView
    private lateinit var voiceStopButton: ImageButton
    private lateinit var generationIndicatorView: TextView
    private lateinit var composer: LinearLayout
    private lateinit var chatScroll: ScrollView
    private var voiceModeActive = false
    private var voiceModeGeneration = 0L
    private lateinit var personalityModesContainer: LinearLayout

    private val executor = Executors.newFixedThreadPool(4)
    private var lastRenderedGenerationOutput = ""
    private lateinit var store: SessionStore
    private val conversation = mutableListOf<UiMessage>()
    private var generationActive = false
    private var activeBubble: TextView? = null
    private var activityVisible = false
    private val generationStateListener: (GenerationState) -> Unit = { state ->
        runOnUiThread { handleGenerationState(state) }
    }
    private var textToSpeech: TextToSpeech? = null
    private var speechRecognizer: SpeechRecognizer? = null
    private var speechListening = false
    private var bargeInMonitoring = false
    private var bargeInCandidateSince = 0L
    private var suppressRecognizerCallbacks = false
    private var ttsTurnGeneration = 0L
    private var activeSpeechUtteranceId: String? = null
    private var activeSpeechText = ""
    private var recentSpokenText = ""
    private var streamingSpeechCursor = 0
    private var streamingSpeechRequestId: String? = null
    private var recentSpeechEndedAt = 0L
    private var edgeSwipeTracking = false
    private var edgeDownX = 0f
    private var edgeDownY = 0f
    private var personalitySelectionGeneration = 0L
    private val personalitySyncLock = Any()
    private val memorySyncLock = Any()
    private var memorySyncRunning = false
    private var currentTab = Tab.CHAT
    private var wasBackgrounded = false
    @Volatile private var generationTransitionVersion = 0L
    private var activeRequestId: String? = null
    private var activeIdempotencyKey: String? = null
    private var connectivityManager: ConnectivityManager? = null
    private var connectivityCallback: ConnectivityManager.NetworkCallback? = null
    private var scheduledHealthCheck: Runnable? = null
    private var pendingLocationAwareText: String? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        store = SessionStore(this)
        Deep33Api.configureAuth(this)
        store.migrateNaturalVoiceDefault()
        conversation.addAll(store.loadMessages())
        // Warm the authenticated session before the first user turn. This keeps the
        // unavoidable anonymous-auth handshake out of perceived chat latency.
        executor.execute {
            runCatching {
                SupabaseAuthManager(this, store.profileId).ensureSession()
            }.onFailure {
                Log.w("DEEP33", "Auth warm-up unavailable: " + it.javaClass.simpleName)
            }
        }

        textToSpeech = TextToSpeech(this) { status ->
            if (status == TextToSpeech.SUCCESS) {
                textToSpeech?.setAudioAttributes(
                    AudioAttributes.Builder()
                        .setUsage(AudioAttributes.USAGE_ASSISTANT)
                        .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                        .build()
                )
                val locale = Locale("es", "CL")
                val result = textToSpeech?.setLanguage(locale)
                if (result == TextToSpeech.LANG_MISSING_DATA || result == TextToSpeech.LANG_NOT_SUPPORTED) {
                    textToSpeech?.setLanguage(Locale("es"))
                }
                selectNaturalSpanishVoice()
                applyVoiceTone()
            }
        }
        textToSpeech?.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
            override fun onStart(utteranceId: String?) {
                if (utteranceId != activeSpeechUtteranceId) return
                runOnUiThread {
                val pendingRecovery = store.loadPendingTurn() != null
                val generationChangedDuringProbe =
                    generationTransitionVersion != generationVersionAtProbeStart
                val keepConnecting = generationActive ||
                    pendingRecovery ||
                    recoveryAtProbeStart ||
                    generationChangedDuringProbe
                if (keepConnecting) {
                    updateConnection(ConnectionState.CONNECTING)
                    if (!generationActive && !pendingRecovery) scheduleHealthCheck(350L)
                } else {
                    updateConnection(if (online) ConnectionState.ONLINE else ConnectionState.OFFLINE)
                }
                if (::diagnosticsView.isInitialized) {
                    diagnosticsView.text = if (keepConnecting) {
                        "CONECTANDO\\nLa conversación pendiente sigue recuperándose."
                    } else {
                        summary
                    }
                }
            }
            }
        }
    }

    private fun isLocationAwareQuery(text: String): Boolean {
        val normalized = VoiceConversationPolicy.normalizeForComparison(text)
        if (Regex("\\b(ubicacion|donde estoy|mi ubicacion|cerca de mi|cerca mio|near me|nearby)\\b").containsMatchIn(normalized)) {
            return true
        }
        if (Regex("\\b(clima|tiempo|temperatura|pronostico|lluvia|llover|humedad|viento|tormenta|paraguas|calor|frio|helado|helada)\\b").containsMatchIn(normalized)) {
            return true
        }
        return Regex("\\b(hara|estara|como estara|que tan|que tal)\\b[^.?!]{0,60}\\b(calor|frio|helado|helada)\\b").containsMatchIn(normalized)
    }
    private fun hasLocationPermission(): Boolean =
        Deep33LocationProvider.hasPermission(this)

    private fun beginLocationAwareSend(text: String) {
        pendingLocationAwareText = text
        if (!hasLocationPermission()) {
            Log.i("DEEP33_GPS", "GPS_PERMISSION_REQUESTED")
            requestPermissions(
                arrayOf(
                    Manifest.permission.ACCESS_FINE_LOCATION,
                    Manifest.permission.ACCESS_COARSE_LOCATION
                ),
                LOCATION_PERMISSION_REQUEST
            )
            return
        }
        resolveLocationAndSend(text)
    }

    private fun resolveLocationAndSend(text: String) {
        if (!Deep33LocationProvider.isLocationEnabled(this)) {
            Log.w("DEEP33_GPS", "LOCATION_PROVIDERS_DISABLED")
            pendingLocationAwareText = null
            sendMessageInternal(text, null)
            return
        }

        if (::statusView.isInitialized) {
            statusView.text = "● GPS"
            statusView.contentDescription = "Obteniendo ubicación GPS para esta consulta"
        }

        Deep33LocationProvider.resolve(
            this,
            callback = { location ->
                runOnUiThread {
                    pendingLocationAwareText = null
                    if (location != null) {
                        Log.i(
                            "DEEP33_GPS",
                            "GPS_LOCATION_READY label=${location.label.orEmpty()} accuracy_m=${location.accuracyMeters} age_ms=${location.ageMs}"
                        )
                    } else {
                        Log.w("DEEP33_GPS", "GPS_LOCATION_UNAVAILABLE")
                    }
                    sendMessageInternal(text, location)
                }
            }
        )
    }

    private fun sendMessage(textOverride: String? = null) {
        val text = (textOverride ?: input.text.toString()).trim()
        Log.i("DEEP33_PERF", "PERF T0_INPUT text_length=${text.length}")
        if (text.isEmpty() || generationActive) return

        if (store.loadPendingTurn() != null) {
            restorePendingTurnIfNeeded()
            return
        }

        if (isLocationAwareQuery(text)) {
            beginLocationAwareSend(text)
            return
        }

        sendMessageInternal(text, null)
    }

    private fun sendMessageInternal(text: String, locationContext: Deep33LocationContext?) {
        if (text.isEmpty() || generationActive) return

        val pendingRecovery = store.loadPendingTurn()
        if (pendingRecovery != null) {
            restorePendingTurnIfNeeded()
            return
        }

        val requestPersonality = Personality.fromKey(store.personality)
        Log.i("DEEP33", "TURN PERSONALITY: " + requestPersonality.key)

        stopVoiceInput()
        if (voiceModeActive) {
            setVoiceModeUi(true)
            setVoiceState(AvatarState.THINKING)
        }
        setGenerationIndicator(thinking = true)

        conversation.add(UiMessage("user", text))
        store.saveMessages(conversation)
        saveCurrentSummary()
        appendBubble("TÚ", text, Color.rgb(12, 34, 27))
        input.setText("")
        refreshSidebarHistory()

        activeBubble = appendBubble("DEEP33", "PENSANDO…", Color.rgb(42, 12, 18))
        activeBubble?.tag = "PENSANDO…"
        sendButton.isEnabled = false
        input.isEnabled = false
        micButton.isEnabled = false
        cancelButton.visibility = View.VISIBLE
        updateConnection(ConnectionState.CONNECTING)

        val payload = buildModelPayload(locationContext)

        val requestId = UUID.randomUUID().toString()
        val idempotencyKey = "chat-" + requestId
        val sessionId = store.sessionId

        store.savePendingTurn(
            PendingTurn(
                profileId = store.profileId,
                sessionId = sessionId,
                requestId = requestId,
                idempotencyKey = idempotencyKey,
                personality = requestPersonality.key,
                payloadJson = payload.toString(),
                conversationJson = serializeConversation(conversation)
            )
        )
        lastRenderedGenerationOutput = ""
        streamingSpeechCursor = 0
        streamingSpeechRequestId = requestId
        launchGeneration(payload, sessionId, requestPersonality, requestId, idempotencyKey)
    }

    private fun restorePendingTurnIfNeeded() {
        val state = store.loadGenerationState()
        if (state?.status == GenerationStatus.DONE && state.sessionId == store.sessionId) {
            conversation.clear()
            conversation.addAll(store.loadMessages())
            // Final messages were committed before DONE. Clear the recovery markers
            // together so a process death between DONE and pending cleanup cannot replay
            // the same request on the next Activity instance.
            store.clearCompletedGeneration(state.requestId)
            generationActive = false
            activeRequestId = null
            activeIdempotencyKey = null
            activeBubble = null
            renderConversation()
            refreshSidebarHistory()
            return
        }

        val pending = store.loadPendingTurn() ?: return

        if (state?.status == GenerationStatus.CANCELLING && state.requestId == pending.requestId) {
            store.finalizeGenerationCancellation(
                pending.requestId,
                pending.sessionId,
                Personality.fromKey(pending.personality).key
            )
            return
        }

        if (
            state?.status == GenerationStatus.RETRYABLE &&
            state.requestId == pending.requestId &&
            pending.sessionId == store.sessionId
        ) {
            activeRequestId = null
            activeIdempotencyKey = null
            activeBubble = appendBubble(
                "DEEP33",
                state.error.ifBlank { "La conexión con DEEP33 no pudo recuperarse. Pulsa reintentar." },
                Color.rgb(42, 12, 18)
            )
            activeBubble?.tag = state.error
            sendButton.isEnabled = true
            input.isEnabled = true
            micButton.isEnabled = true
            cancelButton.visibility = View.INVISIBLE
            updateConnection(ConnectionState.CONNECTING)
            return
        }

        if (generationActive) return

        val payload = try {
            org.json.JSONArray(pending.payloadJson)
        } catch (_: Exception) {
            store.clearPendingTurn(pending.requestId)
            return
        }

        val requestPersonality = Personality.fromKey(pending.personality)
        generationActive = true
        activeRequestId = pending.requestId
        activeIdempotencyKey = pending.idempotencyKey

        val partial = state
            ?.takeIf { it.requestId == pending.requestId }
            ?.partialOutput
            .orEmpty()
        lastRenderedGenerationOutput = ""
        activeBubble = appendBubble(
            "DEEP33",
            partial.ifBlank { "PENSANDO…" },
            Color.rgb(42, 12, 18)
        )
        activeBubble?.tag = partial.ifBlank { "PENSANDO…" }
        setGenerationIndicator(thinking = partial.isBlank())

        sendButton.isEnabled = false
        input.isEnabled = false
        micButton.isEnabled = false
        cancelButton.visibility = View.VISIBLE
        updateConnection(ConnectionState.CONNECTING)

        launchGeneration(
            payload,
            pending.sessionId,
            requestPersonality,
            pending.requestId,
            pending.idempotencyKey
        )
    }

    private fun startGenerationMonitor() {
        if (generationActive || store.loadPendingTurn() != null) {
            monitorGeneration()
        }
    }

    private fun serializeConversation(messages: List<UiMessage>): String {
        val json = org.json.JSONArray()
        messages.forEach { message ->
            if (message.role !in setOf("user", "assistant")) return@forEach
            json.put(
                org.json.JSONObject()
                    .put("role", message.role)
                    .put("content", message.content)
            )
        }
        return json.toString()
    }

    private fun buildModelPayload(locationContext: Deep33LocationContext? = null): org.json.JSONArray {
        val selected = GenerationPerformancePolicy.selectModelContext(conversation)
        val payload = org.json.JSONArray()
        val lastUserIndex = selected.indexOfLast { it.role == "user" }
        var chars = 0

        selected.forEachIndexed { index, message ->
            val content = if (locationContext != null && message.role == "user" && index == lastUserIndex) {
                message.content + "\n\n" + locationContext.asPromptContext()
            } else {
                message.content
            }
            chars += content.length
            payload.put(
                org.json.JSONObject()
                    .put("role", message.role)
                    .put("content", content)
            )
        }

        Log.i(
            "DEEP33",
            "MODEL_CONTEXT messages=${selected.size} chars=${chars} gps=${locationContext != null}"
        )
        return payload
    }

    private fun monitorGeneration() {
        if (!generationActive) return
        store.loadGenerationState()?.let { handleGenerationState(it) }
    }

    private fun handleGenerationState(state: GenerationState) {
        if (!generationActive || state.requestId != activeRequestId) return
        when (state.status) {
            GenerationStatus.RUNNING -> {
                val output = state.partialOutput.trim()
                if (output.isBlank()) {
                    setGenerationIndicator(thinking = true)
                    setVoiceState(AvatarState.THINKING)
                }
                if (output.isNotBlank() && lastRenderedGenerationOutput.isBlank()) {
                    Log.i(
                        "DEEP33_PERF",
                        "PERF T10_FIRST_VISIBLE request_id=" + state.requestId
                    )
                }
                if (output.isNotBlank() && output != lastRenderedGenerationOutput) {
                    activeBubble?.let {
                        it.tag = output
                        renderMarkdown(it, output, isAssistant = true)
                    }
                    lastRenderedGenerationOutput = output
                    setGenerationIndicator(thinking = false, responding = true)
                    setVoiceState(AvatarState.SPEAKING)
                    speakStreamingSentences(output, Personality.fromKey(state.personality))
                }
            }
            GenerationStatus.DONE -> {
                val finalText = state.finalText.trim()
                conversation.clear()
                conversation.addAll(store.loadMessages())
                renderConversation()
                store.clearGenerationState(state.requestId)
                setGenerationIndicator(thinking = false)
                generationActive = false
                activeRequestId = null
                activeIdempotencyKey = null
                activeBubble = null
                cleanupGeneration(true)
                refreshSidebarHistory()
                if (activityVisible && finalText.isNotBlank()) {
                    val remainder = finalText.drop(streamingSpeechCursor).trim()
                    if (remainder.isNotBlank()) {
                        speakAssistant(remainder, Personality.fromKey(state.personality), queue = streamingSpeechCursor > 0)
                    }
                }
                streamingSpeechCursor = 0
                streamingSpeechRequestId = null
                scheduleHealthCheck(350L)
            }
            GenerationStatus.CANCELLING -> {
                setGenerationIndicator(thinking = false)
                setVoiceState(AvatarState.IDLE)
                activeBubble?.let {
                    it.tag = "Generación cancelando…"
                    renderMarkdown(it, "Generación cancelando…")
                }
            }
            GenerationStatus.RETRYABLE -> {
                val message = state.error.ifBlank { "La conexión con DEEP33 no pudo recuperarse. Pulsa reintentar." }
                activeBubble?.let {
                    it.tag = message
                    renderMarkdown(it, message)
                }
                generationActive = false
                activeRequestId = null
                activeIdempotencyKey = null
                sendButton.isEnabled = true
                input.isEnabled = true
                micButton.isEnabled = true
                cancelButton.visibility = View.INVISIBLE
                setGenerationIndicator(thinking = false)
                updateConnection(ConnectionState.CONNECTING)
                setVoiceState(AvatarState.IDLE)
                refreshVoiceStopControl()
                if (!voiceModeActive) setVoiceModeUi(false)
            }
            GenerationStatus.FAILED, GenerationStatus.CANCELLED -> {
                val message = state.error.ifBlank {
                    if (state.status == GenerationStatus.CANCELLED) "Generación cancelada."
                    else "Error de comunicación con DEEP33."
                }
                activeBubble?.let {
                    it.tag = message
                    renderMarkdown(it, message)
                }
                store.clearGenerationState(state.requestId)
                generationActive = false
                activeRequestId = null
                activeIdempotencyKey = null
                cleanupGeneration(false)
            }
        }
    }

    private fun launchGeneration(
        payload: org.json.JSONArray,
        sessionId: String,
        requestPersonality: Personality,
        requestId: String,
        idempotencyKey: String
    ) {
        generationTransitionVersion += 1L
        generationActive = true
        activeRequestId = requestId
        activeIdempotencyKey = idempotencyKey
        Deep33GenerationService.start(this, requestId)
        startGenerationMonitor()
    }

    private fun cancelGeneration() {
        if (!generationActive) return
        val requestId = activeRequestId ?: store.loadPendingTurn()?.requestId ?: return
        val pending = store.loadPendingTurn()
        val sessionId = pending?.sessionId ?: store.sessionId
        val personality = pending?.personality ?: store.personality
        val requested = store.requestGenerationCancellation(
            requestId,
            sessionId,
            Personality.fromKey(personality).key
        )
        if (!requested) return
        Deep33GenerationService.cancel(this, requestId)
        generationActive = false
        activeRequestId = null
        activeIdempotencyKey = null
        activeBubble?.let {
            it.tag = "Generación cancelada."
            renderMarkdown(it, "Generación cancelada.")
        }
        cleanupGeneration(false)
    }

    private fun stopAssistantResponse() {
        if (generationActive) {
            cancelGeneration()
        }
        val hadSpeech = textToSpeech?.isSpeaking == true || activeSpeechUtteranceId != null
        if (hadSpeech || bargeInMonitoring) {
            interruptAssistantSpeech(resumeListening = voiceModeActive)
        } else {
            stopBargeInMonitoring()
            setVoiceState(AvatarState.IDLE)
            refreshVoiceStopControl()
            if (voiceModeActive) {
                setVoiceModeUi(true)
                scheduleNextVoiceTurn(350L)
            }
        }
    }

    private fun cleanupGeneration(success: Boolean) {
        activeBubble = null
        lastRenderedGenerationOutput = ""
        setGenerationIndicator(thinking = false)
        activeRequestId = null
        activeIdempotencyKey = null
        sendButton.isEnabled = true
        input.isEnabled = true
        micButton.isEnabled = true
        cancelButton.visibility = View.GONE

        // A failed generation is not proof that the network is offline. Re-probe the
        // transport instead of presenting a false OFFLINE state to the user.
        if (success) {
            updateConnection(ConnectionState.ONLINE)
        } else {
            updateConnection(ConnectionState.CONNECTING)
            checkConnectivity()
        }

        if (!success || textToSpeech?.isSpeaking != true) {
            setVoiceState(AvatarState.IDLE)
            if (!voiceModeActive) setVoiceModeUi(false)
        }
    }

    private fun neonPanel(fill: Int, stroke: Int): GradientDrawable =
        GradientDrawable().apply {
            setColor(fill)
            cornerRadius = dp(13).toFloat()
            setStroke(dp(1), stroke)
        }

    private fun isGenerationRecoveryActive(): Boolean {
        if (store.loadPendingTurn() != null) return true
        val state = store.loadGenerationState() ?: return false
        return state.sessionId == store.sessionId && state.status in setOf(
            GenerationStatus.RUNNING,
            GenerationStatus.CANCELLING,
            GenerationStatus.RETRYABLE
        )
    }

    private fun updateConnection(state: ConnectionState) {
        // A pending generation is a recoverable transport state, not proof that
        // the device or DEEP33 is offline. Keep the UI in CONNECTING until the
        // service reaches a terminal state or a real health probe confirms failure.
        val visibleState = if (state == ConnectionState.OFFLINE && isGenerationRecoveryActive()) {
            ConnectionState.CONNECTING
        } else {
            state
        }
        val text = when (visibleState) {
            ConnectionState.CONNECTING -> "● CONECTANDO"
            ConnectionState.ONLINE -> "● ONLINE"
            ConnectionState.OFFLINE -> "● OFFLINE"
        }
        if (::statusView.isInitialized) {
            statusView.text = text
            setHeaderStatusStyle(statusView, visibleState)
        }
    }

    private fun selectNaturalSpanishVoice() {
        val tts = textToSpeech ?: return
        val spanishVoices = tts.voices.orEmpty()
            .filter { it.locale.language.equals("es", ignoreCase = true) }

        val preferred = spanishVoices
            .filter { !it.isNetworkConnectionRequired }
            .maxByOrNull { voice ->
                val chileBonus = if (voice.locale.country.equals("CL", ignoreCase = true)) 10_000 else 0
                chileBonus + voice.quality
            }
            ?: spanishVoices.maxByOrNull { it.quality }

        if (preferred != null) {
            runCatching { tts.voice = preferred }
        }
    }

    private fun applyVoiceTone(
        personality: Personality = Personality.fromKey(store.personality)
    ) {
        val tts = textToSpeech ?: return
        val tone = VoiceTone.fromKey(store.voiceTone)
        val profile = VoiceProfileCalculator.calculate(tone, personality)
        tts.setPitch(profile.pitch)
        tts.setSpeechRate(profile.speechRate)
    }

    private fun setVoiceModeUi(active: Boolean) {
        if (!::voicePanel.isInitialized) return
        if (voiceModeActive != active) {
            voiceModeGeneration++
        }
        voiceModeActive = active
        if (active) {
            voicePanel.visibility = View.VISIBLE
            voicePanel.gravity = Gravity.CENTER_HORIZONTAL
            voicePanel.layoutParams = LinearLayout.LayoutParams(-1, 0, 1f)
            chatScroll.visibility = View.GONE
            composer.visibility = View.GONE
            generationIndicatorView.visibility = View.GONE
            voiceStateView.visibility = View.VISIBLE
            val maxAvatar = minOf(dp(252), (resources.displayMetrics.widthPixels - dp(52)).coerceAtLeast(dp(190)))
            avatarView.layoutParams = LinearLayout.LayoutParams(maxAvatar, maxAvatar).apply {
                gravity = Gravity.CENTER
            }
        } else {
            voicePanel.visibility = View.GONE
            voicePanel.gravity = Gravity.CENTER_VERTICAL
            voicePanel.layoutParams = LinearLayout.LayoutParams(-1, ViewGroup.LayoutParams.WRAP_CONTENT)
            chatScroll.visibility = View.VISIBLE
            composer.visibility = View.VISIBLE
            voiceStateView.visibility = View.GONE
            setGenerationIndicator(
                thinking = generationActive && lastRenderedGenerationOutput.isBlank(),
                responding = generationActive && lastRenderedGenerationOutput.isNotBlank()
            )
            avatarView.layoutParams = LinearLayout.LayoutParams(dp(86), dp(86))
        }
        refreshVoiceStopControl()
    }

    private fun scheduleNextVoiceTurn(delayMs: Long = 250L) {
        if (!voiceModeActive ||
            speechListening ||
            bargeInMonitoring ||
            textToSpeech?.isSpeaking == true ||
            generationActive
        ) return

        val generation = voiceModeGeneration
        window.decorView.postDelayed({
            if (voiceModeActive &&
                voiceModeGeneration == generation &&
                !speechListening &&
                !bargeInMonitoring &&
                textToSpeech?.isSpeaking != true &&
                !generationActive
            ) {
                startVoiceInput()
            }
        }, delayMs)
    }

    private fun armBargeInMonitoring(delayMs: Long = 120L) {
        // Never start barge-in recognition while streaming a generated answer.
        // The recognizer can hear the assistant's own TTS output and falsely
        // interrupt speech, producing audible silence mid-response.
        if (generationActive || streamingSpeechRequestId != null) return
        if (speechRecognizer == null ||
            checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED ||
            activeSpeechUtteranceId == null ||
            textToSpeech?.isSpeaking != true
        ) return

        val generation = ttsTurnGeneration
        window.decorView.postDelayed({
            if (generation == ttsTurnGeneration &&
                activeSpeechUtteranceId != null &&
                textToSpeech?.isSpeaking == true &&
                !speechListening
            ) {
                startBargeInMonitoring()
            }
        }, delayMs)
    }

    private fun startBargeInMonitoring() {
        if (bargeInMonitoring ||
            speechRecognizer == null ||
            checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED ||
            activeSpeechUtteranceId == null ||
            textToSpeech?.isSpeaking != true
        ) return

        bargeInMonitoring = true
        bargeInCandidateSince = 0L
        speechListening = true

        val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
            putExtra(
                RecognizerIntent.EXTRA_LANGUAGE_MODEL,
                RecognizerIntent.LANGUAGE_MODEL_FREE_FORM
            )
            putExtra(RecognizerIntent.EXTRA_LANGUAGE, "es-CL")
            putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
            putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 1)
        }
        speechRecognizer?.startListening(intent)
    }

    private fun stopBargeInMonitoring() {
        bargeInMonitoring = false
        bargeInCandidateSince = 0L
        speechListening = false
        speechRecognizer?.cancel()
    }

    private fun interruptAssistantSpeech(resumeListening: Boolean) {
        ttsTurnGeneration++
        activeSpeechUtteranceId = null
        activeSpeechText = ""
        bargeInMonitoring = false
        bargeInCandidateSince = 0L
        suppressRecognizerCallbacks = true
        speechListening = false
        speechRecognizer?.cancel()
        textToSpeech?.stop()

        setVoiceState(AvatarState.IDLE)
        refreshVoiceStopControl()
        if (resumeListening && voiceModeActive) {
            setVoiceModeUi(true)
            window.decorView.postDelayed({
                suppressRecognizerCallbacks = false
                if (voiceModeActive && !generationActive && textToSpeech?.isSpeaking != true) {
                    startVoiceInput()
                }
            }, 300L)
        } else {
            window.decorView.postDelayed({ suppressRecognizerCallbacks = false }, 300L)
            if (!voiceModeActive) setVoiceModeUi(false)
        }
    }

    private fun speakStreamingSentences(
        text: String,
        personality: Personality
    ) {
        if (!store.voiceEnabled && !voiceModeActive) return
        val requestId = activeRequestId ?: return
        if (streamingSpeechRequestId != requestId) {
            streamingSpeechRequestId = requestId
            streamingSpeechCursor = 0
        }
        if (text.length <= streamingSpeechCursor) return

        val tail = text.substring(streamingSpeechCursor)
        val boundary = Regex("(?s)^.*?[.!?…](?:\\s+|$)").find(tail)?.value ?: return
        val consumed = boundary.length
        val phrase = VoiceConversationPolicy.compactForSpeech(boundary)
        if (phrase.isBlank()) {
            streamingSpeechCursor += consumed
            return
        }

        val utteranceId = "deep33-stream-" + System.currentTimeMillis() + "-" + (++ttsTurnGeneration)
        if (streamingSpeechCursor == 0) {
            Log.i(
                "DEEP33_PERF",
                "PERF T11_FIRST_SPOKEN request_id=" + requestId
            )
        }
        streamingSpeechCursor += consumed
        activeSpeechUtteranceId = utteranceId
        activeSpeechText = phrase
        applyVoiceTone(personality)
        if (voiceModeActive) {
            setVoiceModeUi(true)
            setVoiceState(AvatarState.SPEAKING)
        }
        textToSpeech?.speak(
            phrase,
            TextToSpeech.QUEUE_ADD,
            null,
            utteranceId
        )
    }

    private fun speakAssistant(
        text: String,
        personality: Personality = Personality.fromKey(store.personality),
        queue: Boolean = false
    ) {
        if ((!store.voiceEnabled && !voiceModeActive) || text.isBlank()) return
        if (voiceModeActive) {
            setVoiceModeUi(true)
            setVoiceState(AvatarState.SPEAKING)
        }
        applyVoiceTone(personality)

        val speech = VoiceConversationPolicy.compactForSpeech(text)
        if (speech.isBlank()) return

        val utteranceId = "deep33-response-" + System.currentTimeMillis() + "-" + (++ttsTurnGeneration)
        activeSpeechUtteranceId = utteranceId
        activeSpeechText = speech
        textToSpeech?.speak(
            speech,
            if (queue) TextToSpeech.QUEUE_ADD else TextToSpeech.QUEUE_FLUSH,
            null,
            utteranceId
        )
    }

    private fun buildEmptyState(): View {
        val panel = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER
            setPadding(dp(28), dp(24), dp(28), dp(30))
        }
        panel.addView(TextView(this).apply {
            text = "DEEP33"
            textSize = 26f
            letterSpacing = 0.16f
            setTextColor(Deep33Theme.TEXT)
            setTypeface(typeface, android.graphics.Typeface.BOLD)
            gravity = Gravity.CENTER
        })
        panel.addView(View(this).apply {
            setBackgroundColor(Deep33Theme.RED)
            layoutParams = LinearLayout.LayoutParams(dp(32), dp(1)).apply {
                topMargin = dp(10)
                bottomMargin = dp(14)
            }
        })
        panel.addView(TextView(this).apply {
            text = "Intercambia ideas. Explora. Cuestiona."
            textSize = 14f
            setTextColor(Deep33Theme.TEXT_MUTED)
            gravity = Gravity.CENTER
        }, LinearLayout.LayoutParams(-1, ViewGroup.LayoutParams.WRAP_CONTENT))
        panel.addView(TextView(this).apply {
            text = "Internet, memoria, personalidad y voz integradas en una sola conversación."
            textSize = 12.5f
            setTextColor(Color.rgb(112, 118, 128))
            gravity = Gravity.CENTER
            setPadding(dp(12), dp(10), dp(12), 0)
        }, LinearLayout.LayoutParams(-1, ViewGroup.LayoutParams.WRAP_CONTENT))
        return panel
    }

    private fun renderConversation() {
        if (!::chatContainer.isInitialized) return
        chatContainer.removeAllViews()
        if (conversation.isEmpty()) {
            chatContainer.addView(
                buildEmptyState(),
                LinearLayout.LayoutParams(-1, 0, 1f)
            )
        } else {
            conversation.takeLast(50).forEach { message ->
                appendBubble(
                    if (message.role == "user") "TÚ" else "DEEP33",
                    message.content,
                    if (message.role == "user") Deep33Theme.USER_BUBBLE else Deep33Theme.ASSISTANT_BUBBLE
                )
            }
        }
        chatContainer.post { scrollToBottom() }
    }

    private fun appendBubble(label: String, content: String, background: Int): TextView {
        val isAssistant = label == "DEEP33"
        val accent = Personality.fromKey(store.personality).accent
        val bubble = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(15), dp(12), dp(15), dp(12))
            setBackground(
                GradientDrawable().apply {
                    setColor(background)
                    cornerRadius = dp(18).toFloat()
                    setStroke(dp(1), if (isAssistant) Color.rgb(74, 18, 30) else Deep33Theme.LINE)
                }
            )
        }

        val headerRow = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        headerRow.addView(TextView(this).apply {
            text = label
            setTextColor(if (isAssistant) accent else Deep33Theme.TEXT_MUTED)
            textSize = 10f
            letterSpacing = 0.10f
            setTypeface(typeface, android.graphics.Typeface.BOLD)
        })
        if (isAssistant) {
            headerRow.addView(View(this).apply {
                setBackgroundColor(accent)
                layoutParams = LinearLayout.LayoutParams(dp(18), dp(1)).apply {
                    leftMargin = dp(8)
                }
            })
        }
        bubble.addView(headerRow)

        val contentView = TextView(this).apply {
            tag = content
            textSize = 16f
            letterSpacing = 0.008f
            includeFontPadding = false
            setTextColor(Deep33Theme.TEXT)
            setPadding(0, dp(7), 0, 0)
            movementMethod = LinkMovementMethod.getInstance()
            maxWidth = (resources.displayMetrics.widthPixels *
                if (isAssistant) 0.92f else 0.82f).roundToInt().coerceAtLeast(dp(120))
        }
        bubble.addView(contentView)
        renderMarkdown(contentView, content, isAssistant)

        if (isAssistant && content.trim() != "Pensando...") {
            addAssistantActionRow(
                bubble = bubble,
                contentView = contentView,
                content = content
            )
        }

        val params = LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.WRAP_CONTENT,
            ViewGroup.LayoutParams.WRAP_CONTENT
        ).apply {
            gravity = if (isAssistant) Gravity.START else Gravity.END
            val horizontalInset = dp(10)
            setMargins(
                horizontalInset,
                0,
                horizontalInset,
                dp(10)
            )
        }
        chatContainer.addView(bubble, params)
        bubble.alpha = 1f
        bubble.translationY = 0f
        bubble.post { scrollToBottom() }
        return contentView
    }

    private fun addAssistantActionRow(
        bubble: LinearLayout,
        contentView: TextView,
        content: String
    ) {
        val row = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.START or Gravity.CENTER_VERTICAL
            setPadding(0, dp(5), 0, 0)
        }

        val normalTint = Color.rgb(158, 164, 174)
        val activeTint = Personality.fromKey(store.personality).accent

        fun actionButton(
            icon: Int,
            description: String,
            onClick: (ImageButton) -> Unit
        ): ImageButton =
            ImageButton(this).apply {
                setImageResource(icon)
                imageTintList = android.content.res.ColorStateList.valueOf(normalTint)
                scaleType = android.widget.ImageView.ScaleType.CENTER_INSIDE
                setPadding(dp(9), dp(9), dp(9), dp(9))
                background = iconCircleBackground(Deep33Theme.SURFACE_2, Deep33Theme.LINE_SOFT)
                contentDescription = description
                minimumWidth = 0
                minimumHeight = 0
                layoutParams = LinearLayout.LayoutParams(dp(40), dp(40)).apply {
                    rightMargin = dp(4)
                }
                addPressFeedback(this)
                setOnClickListener { onClick(this) }
            }

        row.addView(
            actionButton(
                R.drawable.ic_action_copy,
                "Copiar respuesta"
            ) { button ->
                val clipboard = getSystemService(CLIPBOARD_SERVICE) as ClipboardManager
                clipboard.setPrimaryClip(ClipData.newPlainText("DEEP33", content))
                button.imageTintList =
                    android.content.res.ColorStateList.valueOf(activeTint)
                button.postDelayed({
                    button.imageTintList =
                        android.content.res.ColorStateList.valueOf(normalTint)
                }, 650L)
            }
        )

        row.addView(
            actionButton(
                R.drawable.ic_action_volume,
                "Leer respuesta en voz alta"
            ) { button ->
                speakAssistant(content, Personality.fromKey(store.personality))
                button.imageTintList =
                    android.content.res.ColorStateList.valueOf(activeTint)
                button.postDelayed({
                    button.imageTintList =
                        android.content.res.ColorStateList.valueOf(normalTint)
                }, 900L)
            }
        )

        val likeButton = actionButton(
            R.drawable.ic_action_like,
            "Respuesta útil"
        ) { button ->
            button.imageTintList =
                android.content.res.ColorStateList.valueOf(activeTint)
            dislikeButtonTint(normalTint, likeButton = button, other = null)
            sendResponseFeedback(content, useful = true)
        }
        row.addView(likeButton)

        val dislikeButton = actionButton(
            R.drawable.ic_action_dislike,
            "Respuesta no útil"
        ) { button ->
            button.imageTintList =
                android.content.res.ColorStateList.valueOf(activeTint)
            dislikeButtonTint(normalTint, likeButton = null, other = button)
            sendResponseFeedback(content, useful = false)
        }
        row.addView(dislikeButton)

        // One final compact action, matching the common response-action pattern.
        row.addView(
            actionButton(
                R.drawable.ic_action_retry,
                "Intentar de nuevo"
            ) {
                regenerateLastResponse()
            }
        )

        bubble.addView(
            row,
            LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                dp(40)
            )
        )
    }

    private fun dislikeButtonTint(
        normalTint: Int,
        likeButton: ImageButton?,
        other: ImageButton?
    ) {
        // The feedback selection is intentionally local to this bubble.
        if (likeButton != null) {
            likeButton.imageTintList =
                android.content.res.ColorStateList.valueOf(Personality.fromKey(store.personality).accent)
        }
        if (other != null) {
            other.imageTintList =
                android.content.res.ColorStateList.valueOf(Personality.fromKey(store.personality).accent)
        }
    }

    private fun sendResponseFeedback(content: String, useful: Boolean) {
        val response = content.trim()
        if (response.isBlank()) return
        val sessionId = store.sessionId
        val responseHash = MessageDigest.getInstance("SHA-256")
            .digest(response.toByteArray(Charsets.UTF_8))
            .joinToString("") { "%02x".format(it) }
        val requestId = store.loadGenerationState()
            ?.takeIf { it.sessionId == sessionId && it.finalText == response }
            ?.requestId
            ?: responseHash
        executor.execute {
            runCatching {
                Deep33Api.sendFeedback(
                    sessionId = sessionId,
                    requestId = requestId,
                    rating = if (useful) "useful" else "not_useful",
                    responseHash = responseHash,
                )
            }.onFailure {
                Log.w("DEEP33", "Feedback deferred: " + it.javaClass.simpleName)
            }
        }
    }

    private fun regenerateLastResponse() {
        if (generationActive) return

        val pending = store.loadPendingTurn()
        val state = store.loadGenerationState()
        if (
            pending != null &&
            state?.status == GenerationStatus.RETRYABLE &&
            state.requestId == pending.requestId &&
            pending.sessionId == store.sessionId
        ) {
            val payload = runCatching { org.json.JSONArray(pending.payloadJson) }.getOrNull() ?: return
            val requestPersonality = Personality.fromKey(pending.personality)
            activeBubble?.let { chatContainer.removeView(it) }
            activeBubble = appendBubble("DEEP33", "Pensando...", Color.rgb(42, 12, 18))
            activeBubble?.tag = "Pensando..."
            generationActive = true
            activeRequestId = pending.requestId
            activeIdempotencyKey = pending.idempotencyKey
            lastRenderedGenerationOutput = ""
            sendButton.isEnabled = false
            input.isEnabled = false
            micButton.isEnabled = false
            cancelButton.visibility = View.VISIBLE
            updateConnection(ConnectionState.CONNECTING)
            store.saveGenerationState(
                status = GenerationStatus.RUNNING,
                requestId = pending.requestId,
                sessionId = pending.sessionId,
                personality = requestPersonality.key,
                partialOutput = "",
                error = "",
                durable = true
            )
            launchGeneration(
                payload,
                pending.sessionId,
                requestPersonality,
                pending.requestId,
                pending.idempotencyKey
            )
            return
        }

        val assistantIndex = conversation.indexOfLast { it.role == "assistant" }
        if (assistantIndex < 0) return

        val userIndex = conversation
            .take(assistantIndex)
            .indexOfLast { it.role == "user" }

        if (userIndex < 0) return

        val prompt = conversation[userIndex].content
        while (conversation.size > userIndex) {
            conversation.removeAt(userIndex)
        }

        store.saveMessages(conversation)
        saveCurrentSummary()
        renderConversation()
        sendMessage(prompt)
    }
    private fun renderMarkdown(view: TextView, markdown: String, isAssistant: Boolean = false) {
        view.text = MarkdownRenderer.render(
            markdown,
            suppressAssistantSources = isAssistant
        )
        view.movementMethod = LinkMovementMethod.getInstance()
    }

    private fun scrollToBottom() {
        val scroll = chatContainer.parent as? ScrollView ?: return
        scroll.post { scroll.smoothScrollTo(0, chatContainer.height) }
    }

    private fun setVoiceState(state: AvatarState) {
        if (!::avatarView.isInitialized) return
        avatarView.setVoiceState(state)
        voiceStateView.text = when (state) {
            AvatarState.IDLE -> "MODO VOZ"
            AvatarState.LISTENING -> "ESCUCHANDO"
            AvatarState.THINKING -> "PENSANDO…"
            AvatarState.SPEAKING -> "DEEP33 ESTÁ RESPONDIENDO…"
        }
        voiceStateView.visibility = if (voiceModeActive) View.VISIBLE else View.GONE
        refreshVoiceStopControl()
    }

    private fun toggleVoiceInput() {
        if (speechListening && !bargeInMonitoring) {
            stopVoiceInput()
            return
        }
        if (textToSpeech?.isSpeaking == true) {
            interruptAssistantSpeech(resumeListening = voiceModeActive)
            return
        }
        startVoiceInput()
    }

    private fun startVoiceInput() {
        if (speechRecognizer == null) {
            Toast.makeText(this, "El dispositivo no tiene reconocimiento de voz disponible.", Toast.LENGTH_SHORT).show()
            return
        }
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), VOICE_PERMISSION_REQUEST)
            return
        }

        setVoiceModeUi(true)
        setVoiceState(AvatarState.LISTENING)
        speechListening = true

        val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
            putExtra(
                RecognizerIntent.EXTRA_LANGUAGE_MODEL,
                RecognizerIntent.LANGUAGE_MODEL_FREE_FORM
            )
            putExtra(RecognizerIntent.EXTRA_LANGUAGE, "es-CL")
            putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
            putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 1)
        }
        speechRecognizer?.startListening(intent)
    }

    private fun stopVoiceInput() {
        if (!speechListening && !bargeInMonitoring) return
        bargeInMonitoring = false
        speechListening = false
        speechRecognizer?.stopListening()
        setVoiceState(AvatarState.IDLE)
        if (!generationActive && !voiceModeActive) setVoiceModeUi(false)
    }

    private val recognitionListener = object : RecognitionListener {
        override fun onReadyForSpeech(params: Bundle?) {
            if (suppressRecognizerCallbacks) return
            speechListening = true
            if (bargeInMonitoring) {
                setVoiceState(AvatarState.SPEAKING)
            } else {
                setVoiceState(AvatarState.LISTENING)
            }
        }

        override fun onBeginningOfSpeech() {
            if (suppressRecognizerCallbacks) return
            if (!bargeInMonitoring) {
                setVoiceState(AvatarState.LISTENING)
            }
        }

        override fun onRmsChanged(rmsdB: Float) {
            if (::avatarView.isInitialized) {
                avatarView.setAudioLevel((rmsdB / 10f).coerceIn(0f, 1f))
            }
        }

        override fun onBufferReceived(buffer: ByteArray?) = Unit

        override fun onEndOfSpeech() {
            if (suppressRecognizerCallbacks) return
            speechListening = false
            if (!bargeInMonitoring) {
                setVoiceState(AvatarState.THINKING)
            }
        }

        override fun onError(error: Int) {
            if (suppressRecognizerCallbacks) return
            speechListening = false
            if (bargeInMonitoring) {
                bargeInMonitoring = false
                bargeInCandidateSince = 0L
                if (textToSpeech?.isSpeaking == true && activeSpeechUtteranceId != null) {
                    setVoiceState(AvatarState.SPEAKING)
                    armBargeInMonitoring(250L)
                }
                return
            }

            setVoiceState(AvatarState.IDLE)
            if (voiceModeActive) {
                setVoiceModeUi(true)
                scheduleNextVoiceTurn(450L)
            } else {
                setVoiceModeUi(false)
            }
        }

        override fun onResults(results: Bundle?) {
            if (suppressRecognizerCallbacks) return

            speechListening = false
            val recognized = results
                ?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                ?.firstOrNull()
                .orEmpty()
                .trim()

            if (bargeInMonitoring) {
                bargeInMonitoring = false
                bargeInCandidateSince = 0L
                val recentStop = VoiceConversationPolicy.isStopCommand(recognized) &&
                    (activeSpeechUtteranceId != null || System.currentTimeMillis() - recentSpeechEndedAt <= 1200L)
                if (recognized.isNotBlank() &&
                    (recentStop || VoiceConversationPolicy.isStopCommand(recognized))
                ) {
                    Log.i("DEEP33_VOICE", "BARGE_IN_EXPLICIT_STOP")
                    interruptAssistantSpeech(resumeListening = voiceModeActive)
                } else if (textToSpeech?.isSpeaking == true && activeSpeechUtteranceId != null) {
                    armBargeInMonitoring(120L)
                }
                return
            }

            if (recognized.isNotBlank()) {
                input.setText(recognized)
                input.setSelection(input.text.length)
                sendMessage(recognized)
            } else {
                setVoiceState(AvatarState.IDLE)
                if (voiceModeActive) {
                    setVoiceModeUi(true)
                } else {
                    setVoiceModeUi(false)
                }
            }
        }

        override fun onPartialResults(partialResults: Bundle?) {
            if (suppressRecognizerCallbacks) return

            val partial = partialResults
                ?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                ?.firstOrNull()
                .orEmpty()
                .trim()

            if (bargeInMonitoring) {
                if (partial.isBlank()) return

                // Only an explicit stop command may interrupt TTS.
                // Never use “not an echo” as an interrupt condition because
                // Android speech recognition can mistranscribe the assistant
                // audio and otherwise cut the response off.
                val isExplicitStop = VoiceConversationPolicy.isStopCommand(partial)
                if (isExplicitStop) {
                    Log.i("DEEP33_VOICE", "BARGE_IN_EXPLICIT_STOP_PARTIAL")
                    interruptAssistantSpeech(resumeListening = voiceModeActive)
                } else {
                    bargeInCandidateSince = 0L
                }
                return
            }

            if (partial.isNotBlank() && ::input.isInitialized && input.isEnabled) {
                input.setText(partial)
                input.setSelection(input.text.length)
            }
        }

                override fun onEvent(eventType: Int, params: Bundle?) = Unit
    }

    override fun onRequestPermissionsResult(
        requestCode: Int,
        permissions: Array<out String>,
        grantResults: IntArray
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == VOICE_PERMISSION_REQUEST &&
            grantResults.firstOrNull() == PackageManager.PERMISSION_GRANTED
        ) {
            startVoiceInput()
            return
        }

        if (requestCode == LOCATION_PERMISSION_REQUEST) {
            val text = pendingLocationAwareText ?: return
            if (hasLocationPermission()) {
                resolveLocationAndSend(text)
            } else {
                pendingLocationAwareText = null
                if (::statusView.isInitialized) {
                    statusView.text = "● CONECTANDO"
                    statusView.contentDescription = "Estado de conexión de DEEP33"
                }
                sendMessageInternal(text, null)
            }
        }
    }

    companion object {
        private const val VOICE_PERMISSION_REQUEST = 7001
        private const val LOCATION_PERMISSION_REQUEST = 7002
    }
}
