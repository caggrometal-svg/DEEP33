package cl.caggrometal.deep33

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.os.Bundle
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
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import java.util.Locale
import java.util.UUID
import java.util.concurrent.Executors
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
    private lateinit var sendButton: Button
    private lateinit var micButton: Button
    private lateinit var cancelButton: Button
    private lateinit var statusView: TextView
    private lateinit var currentPersonalityView: TextView
    private lateinit var diagnosticsView: TextView
    private lateinit var voicePanel: LinearLayout
    private lateinit var voiceStateView: TextView
    private lateinit var avatarView: VoiceAvatarView
    private lateinit var composer: LinearLayout
    private lateinit var chatScroll: ScrollView
    private var voiceModeActive = false
    private var voiceModeGeneration = 0L
    private lateinit var personalityModesContainer: LinearLayout

    private val executor = Executors.newFixedThreadPool(2)
    private lateinit var store: SessionStore
    private val conversation = mutableListOf<UiMessage>()
    private var generationActive = false
    private var activeBubble: TextView? = null
    private val generationHandler = Handler(Looper.getMainLooper())
    private var activityVisible = false
    private val generationMonitor = object : Runnable {
        override fun run() {
            monitorGeneration()
        }
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
    private var activeRequestId: String? = null
    private var activeIdempotencyKey: String? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        store = SessionStore(this)
        store.migrateNaturalVoiceDefault()
        conversation.addAll(store.loadMessages())

        textToSpeech = TextToSpeech(this) { status ->
            if (status == TextToSpeech.SUCCESS) {
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
                    setVoiceState(AvatarState.SPEAKING)
                    armBargeInMonitoring()
                }
            }

            override fun onDone(utteranceId: String?) {
                if (utteranceId != activeSpeechUtteranceId) return
                runOnUiThread {
                    recentSpokenText = activeSpeechText
                    recentSpeechEndedAt = System.currentTimeMillis()
                    activeSpeechUtteranceId = null
                    activeSpeechText = ""
                    stopBargeInMonitoring()
                    setVoiceState(AvatarState.IDLE)
                    if (voiceModeActive) {
                        setVoiceModeUi(true)
                        scheduleNextVoiceTurn(700L)
                    } else {
                        // Voice responses enabled from text chat must not force the UI into
                        // persistent voice mode.
                        setVoiceModeUi(false)
                    }
                }
            }

            override fun onError(utteranceId: String?) {
                if (utteranceId != activeSpeechUtteranceId) return
                runOnUiThread {
                    activeSpeechUtteranceId = null
                    activeSpeechText = ""
                    stopBargeInMonitoring()
                    setVoiceState(AvatarState.IDLE)
                    if (!voiceModeActive) {
                        setVoiceModeUi(false)
                    } else {
                        setVoiceModeUi(true)
                        scheduleNextVoiceTurn(450L)
                    }
                }
            }
        })

        if (SpeechRecognizer.isRecognitionAvailable(this)) {
            speechRecognizer = SpeechRecognizer.createSpeechRecognizer(this).apply {
                setRecognitionListener(recognitionListener)
            }
        }

        window.statusBarColor = Deep33Theme.BG
        window.navigationBarColor = Deep33Theme.BG
        window.decorView.setOnApplyWindowInsetsListener { view, insets ->
            @Suppress("DEPRECATION")
            view.setPadding(
                insets.systemWindowInsetLeft,
                insets.systemWindowInsetTop,
                insets.systemWindowInsetRight,
                insets.systemWindowInsetBottom
            )
            insets
        }

        setContentView(buildRoot())
        showTab(Tab.CHAT)
        renderConversation()
        applyPersonalityTheme(Personality.fromKey(store.personality))
        checkConnectivity()
        loadRemoteContext()
        retryPendingMemorySync()
        restorePendingTurnIfNeeded()
    }

    override fun onStart() {
        super.onStart()
        activityVisible = true
        if (wasBackgrounded) {
            // Reattach to the durable generation after returning from another app.
            restorePendingTurnIfNeeded()
            retryPendingMemorySync()
            checkConnectivity()
        }
        retryPendingMemorySync()
        startGenerationMonitor()
        wasBackgrounded = false
    }

    override fun onStop() {
        activityVisible = false
        wasBackgrounded = true
        super.onStop()
    }

    override fun onDestroy() {
        generationHandler.removeCallbacks(generationMonitor)
        stopVoiceInput()
        interruptAssistantSpeech(resumeListening = false)
        executor.shutdownNow()
        speechRecognizer?.destroy()
        speechRecognizer = null
        textToSpeech?.stop()
        textToSpeech?.shutdown()
        textToSpeech = null
        super.onDestroy()
    }

    @Deprecated("Use AndroidX OnBackPressedDispatcher when migrating this screen.")
    override fun onBackPressed() {
        if (sidebar.visibility == View.VISIBLE) {
            hideSidebar()
            return
        }
        if (voiceModeActive) {
            interruptAssistantSpeech(resumeListening = false)
            stopVoiceInput()
            setVoiceState(AvatarState.IDLE)
            setVoiceModeUi(false)
            return
        }
        if (currentTab != Tab.CHAT) {
            showTab(Tab.CHAT)
            return
        }
        super.onBackPressed()
    }

    private enum class Tab { CHAT, STATUS, SETTINGS }

    private fun dp(value: Int): Int =
        (value * resources.displayMetrics.density).roundToInt()

    override fun dispatchTouchEvent(event: android.view.MotionEvent): Boolean {
        when (event.actionMasked) {
            android.view.MotionEvent.ACTION_DOWN -> {
                edgeSwipeTracking = event.x <= dp(28)
                edgeDownX = event.x
                edgeDownY = event.y
            }
            android.view.MotionEvent.ACTION_UP, android.view.MotionEvent.ACTION_CANCEL -> {
                if (edgeSwipeTracking && event.actionMasked == android.view.MotionEvent.ACTION_UP) {
                    val dx = event.x - edgeDownX
                    val dy = kotlin.math.abs(event.y - edgeDownY)
                    if (dx >= dp(72) && dy <= dp(96)) showSidebar()
                }
                edgeSwipeTracking = false
            }
        }
        return super.dispatchTouchEvent(event)
    }

    private fun buildRoot(): View {
        rootFrame = FrameLayout(this).apply {
            setBackgroundColor(Deep33Theme.BG)
        }

        val main = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(Deep33Theme.BG)
            setPadding(dp(14), dp(8), dp(14), dp(8))
        }

        val header = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(0, dp(4), 0, dp(16))
        }

        header.addView(Button(this).apply {
            text = "☰"
            textSize = 22f
            minWidth = 0
            minHeight = 0
            setPadding(0, 0, 0, 0)
            setTextColor(Deep33Theme.TEXT_MUTED)
            setBackgroundColor(Color.TRANSPARENT)
            stateListAnimator = null
            setOnClickListener { toggleSidebar() }
        }, LinearLayout.LayoutParams(dp(48), dp(48)))

        statusView = TextView(this).apply {
            text = "PROCESANDO · DEEP33"
            textSize = 11f
            setPadding(0, dp(2), 0, 0)
        }

        val titleGroup = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER_HORIZONTAL
        }
        titleGroup.addView(TextView(this).apply {
            text = "DEEP33"
            setTextColor(Deep33Theme.TEXT)
            textSize = 29f
            letterSpacing = 0.16f
            setTypeface(android.graphics.Typeface.create("sans-serif", android.graphics.Typeface.BOLD))
        })
        currentPersonalityView = TextView(this).apply {
            textSize = 10.5f
            letterSpacing = 0.14f
            setPadding(0, dp(3), 0, 0)
            setTypeface(typeface, android.graphics.Typeface.BOLD)
        }
        titleGroup.addView(currentPersonalityView)
        titleGroup.addView(View(this).apply {
            setBackgroundColor(Deep33Theme.RED)
            layoutParams = LinearLayout.LayoutParams(dp(26), dp(1)).apply {
                topMargin = dp(7)
            }
        })

        header.addView(
            titleGroup,
            LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        )
        header.addView(View(this), LinearLayout.LayoutParams(dp(48), dp(48)))

        main.addView(header, ViewGroup.LayoutParams(-1, ViewGroup.LayoutParams.WRAP_CONTENT))

        contentFrame = FrameLayout(this)
        main.addView(contentFrame, LinearLayout.LayoutParams(-1, 0, 1f))
        // Conversation navigation was removed from the bottom bar so it cannot
        // replace/rebuild the chat screen and break the active conversation thread.
        rootFrame.addView(main, FrameLayout.LayoutParams(-1, -1))

        drawerScrim = View(this).apply {
            setBackgroundColor(Color.argb(145, 0, 0, 0))
            visibility = View.GONE
            setOnClickListener { hideSidebar() }
        }
        rootFrame.addView(drawerScrim, FrameLayout.LayoutParams(-1, -1))

        sidebar = buildSidebar()
        rootFrame.addView(
            sidebar,
            FrameLayout.LayoutParams(dp(322), -1).apply { gravity = Gravity.START }
        )
        sidebar.visibility = View.GONE
        return rootFrame
    }

    private fun buildSidebar(): LinearLayout {
        val panel = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(18), dp(28), dp(14), dp(16))
            setBackgroundColor(Color.rgb(6, 6, 9))
            elevation = dp(12).toFloat()
        }

        val header = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        header.addView(
            TextView(this).apply {
                text = "DEEP33 · CHATS"
                setTextColor(Color.WHITE)
                textSize = 22f
                setTypeface(typeface, android.graphics.Typeface.BOLD)
            },
            LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        )
        header.addView(Button(this).apply {
            text = "Cerrar"
            setOnClickListener { hideSidebar() }
        })
        panel.addView(header)

        panel.addView(Button(this).apply {
            text = "＋  NUEVO CHAT"
            isAllCaps = false
            setTextColor(Deep33Theme.TEXT)
            setBackground(neonPanel(Deep33Theme.SURFACE_2, Deep33Theme.LINE_SOFT))
            setOnClickListener {
                startNewSession()
                hideSidebar()
            }
        }, ViewGroup.LayoutParams(-1, ViewGroup.LayoutParams.WRAP_CONTENT))

        panel.addView(Button(this).apply {
            text = "CONFIGURACIÓN"
            isAllCaps = false
            setTextColor(Color.rgb(255, 70, 90))
            setBackground(neonPanel(Deep33Theme.RED_SURFACE, Deep33Theme.RED_DEEP))
            setOnClickListener {
                showTab(Tab.SETTINGS)
                hideSidebar()
            }
        }, ViewGroup.LayoutParams(-1, ViewGroup.LayoutParams.WRAP_CONTENT))

        panel.addView(TextView(this).apply {
            text = "MODOS"
            setTextColor(Color.LTGRAY)
            textSize = 12f
            setPadding(0, dp(16), 0, dp(8))
        })

        personalityModesContainer = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
        }
        refreshPersonalityButtons()
        panel.addView(personalityModesContainer)

        panel.addView(TextView(this).apply {
            text = "HISTORIAL"
            setTextColor(Color.LTGRAY)
            textSize = 12f
            setPadding(0, dp(18), 0, dp(8))
        })

        historyContainer = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
        }
        val historyScroll = ScrollView(this).apply {
            isFillViewport = true
            addView(historyContainer)
        }
        panel.addView(historyScroll, LinearLayout.LayoutParams(-1, 0, 1f))
        refreshSidebarHistory()
        return panel
    }

    private fun refreshPersonalityButtons() {
        if (!::personalityModesContainer.isInitialized) return
        personalityModesContainer.removeAllViews()
        Personality.entries.forEach { option ->
            personalityModesContainer.addView(personalityButton(option))
        }
    }

    private fun personalityButton(option: Personality): Button =
        Button(this).apply {
            val active = Personality.fromKey(store.personality) == option
            text = if (active) {
                "✓ " + option.key + " · ACTIVA"
            } else {
                "○ " + option.key
            }
            isAllCaps = false
            gravity = Gravity.START or Gravity.CENTER_VERTICAL
            minHeight = dp(54)
            setTextColor(option.accent)
            setBackground(
                neonPanel(
                    if (active) Deep33Theme.RED_SURFACE else Deep33Theme.SURFACE,
                    option.accent
                )
            )
            setOnClickListener {
                selectPersonality(option)
                hideSidebar()
            }
        }

    private fun refreshSidebarHistory() {
        if (!::historyContainer.isInitialized) return
        historyContainer.removeAllViews()
        val items = store.loadChatSummaries()
        if (items.isEmpty()) {
            historyContainer.addView(TextView(this).apply {
                text = "Todavía no hay conversaciones guardadas."
                setTextColor(Color.GRAY)
                textSize = 13f
                setPadding(0, dp(8), 0, dp(8))
            })
            return
        }
        items.forEach { item ->
            historyContainer.addView(Button(this).apply {
                text = item.title
                isAllCaps = false
                gravity = Gravity.START or Gravity.CENTER_VERTICAL
                minHeight = dp(52)
                setOnClickListener {
                    openSession(item.sessionId)
                    hideSidebar()
                }
            })
        }
    }

    private fun toggleSidebar() {
        if (sidebar.visibility == View.VISIBLE) hideSidebar() else showSidebar()
    }

    private fun showSidebar() {
        refreshSidebarHistory()
        drawerScrim.visibility = View.VISIBLE
        sidebar.visibility = View.VISIBLE
    }

    private fun hideSidebar() {
        drawerScrim.visibility = View.GONE
        sidebar.visibility = View.GONE
    }

    private fun showTab(tab: Tab) {
        currentTab = tab
        contentFrame.removeAllViews()
        when (tab) {
            Tab.CHAT -> contentFrame.addView(buildChat())
            Tab.STATUS -> contentFrame.addView(buildStatus())
            Tab.SETTINGS -> contentFrame.addView(buildSettings())
        }
    }

    private fun buildChat(): View {
        val box = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }

        voicePanel = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(dp(14), dp(8), dp(14), dp(8))
            visibility = View.GONE
            setBackground(
                GradientDrawable().apply {
                    setColor(Deep33Theme.SURFACE)
                    cornerRadius = dp(18).toFloat()
                    setStroke(dp(1), Deep33Theme.LINE)
                }
            )
            setOnClickListener {
                if (textToSpeech?.isSpeaking == true) {
                    interruptAssistantSpeech(resumeListening = voiceModeActive)
                } else {
                    toggleVoiceInput()
                }
            }
        }

        avatarView = VoiceAvatarView(this).apply {
            setPersonality(Personality.fromKey(store.personality))
            setVoiceState(AvatarState.IDLE)
            contentDescription = "Avatar de voz de DEEP33"
        }
        voiceStateView = TextView(this).apply {
            text = "Modo voz"
            setTextColor(Color.WHITE)
            textSize = 15f
            setPadding(dp(14), 0, 0, 0)
        }
        voicePanel.addView(avatarView, LinearLayout.LayoutParams(dp(86), dp(86)))
        voicePanel.addView(
            voiceStateView,
            LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        )
        box.addView(voicePanel, ViewGroup.LayoutParams(-1, ViewGroup.LayoutParams.WRAP_CONTENT))

        chatContainer = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(Deep33Theme.BG)
            setPadding(dp(6), dp(8), dp(6), dp(12))
        }
        chatScroll = ScrollView(this).apply {
            isFillViewport = true
            addView(chatContainer)
        }
        box.addView(chatScroll, LinearLayout.LayoutParams(-1, 0, 1f))

        input = EditText(this).apply {
            hint = ""
            setHintTextColor(Deep33Theme.TEXT_MUTED)
            setTextColor(Deep33Theme.TEXT)
            textSize = 16f
            maxLines = 5
            gravity = Gravity.TOP
            setBackgroundColor(Color.TRANSPARENT)
            // The outer FrameLayout owns the single chat-input bubble.
            // Reserve space inside that bubble for the speaker and send controls.
            setPadding(dp(16), dp(12), dp(112), dp(12))
        }

        val inputBubble = FrameLayout(this).apply {
            setBackground(
                GradientDrawable().apply {
                    setColor(Deep33Theme.SURFACE)
                    cornerRadius = dp(18).toFloat()
                    setStroke(dp(1), Deep33Theme.LINE_SOFT)
                }
            )
        }
        inputBubble.addView(
            input,
            FrameLayout.LayoutParams(-1, dp(58))
        )

        micButton = Button(this).apply {
            text = "🔊"
            contentDescription = "Voz · conversación por voz"
            textSize = 20f
            isAllCaps = false
            minWidth = 0
            minHeight = 0
            setPadding(0, 0, 0, 0)
            setTextColor(Color.WHITE)
            setBackgroundColor(Color.TRANSPARENT)
            elevation = 0f
            setOnClickListener { toggleVoiceInput() }
        }
        inputBubble.addView(
            micButton,
            FrameLayout.LayoutParams(dp(46), dp(46), Gravity.END or Gravity.CENTER_VERTICAL).apply {
                marginEnd = dp(56)
            }
        )

        sendButton = Button(this).apply {
            text = "↑"
            contentDescription = "Enviar mensaje"
            textSize = 28f
            isAllCaps = false
            minWidth = 0
            minHeight = 0
            setPadding(0, 0, 0, dp(2))
            setTextColor(Personality.fromKey(store.personality).accent)
            setBackgroundColor(Color.TRANSPARENT)
            elevation = 0f
            setOnClickListener { sendMessage() }
        }
        inputBubble.addView(
            sendButton,
            FrameLayout.LayoutParams(dp(50), dp(46), Gravity.END or Gravity.CENTER_VERTICAL).apply {
                marginEnd = dp(4)
            }
        )

        cancelButton = Button(this).apply {
            text = STOP_BUTTON_GLYPH
            contentDescription = "Detener generación"
            textSize = 18f
            isAllCaps = false
            minWidth = 0
            minHeight = 0
            gravity = Gravity.CENTER
            setPadding(0, 0, 0, 0)
            visibility = View.GONE
            setOnClickListener { cancelGeneration() }
        }
        applyStopButtonTheme(Personality.fromKey(store.personality))

        composer = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.BOTTOM
            setPadding(0, dp(8), 0, 0)
            addView(inputBubble, LinearLayout.LayoutParams(0, dp(58), 1f).apply {
                setMargins(0, 0, dp(6), 0)
            })
            addView(cancelButton, LinearLayout.LayoutParams(dp(48), dp(48)).apply {
                setMargins(0, 0, dp(6), 0)
            })
        }
        box.addView(composer)
        return box
    }

    private fun buildStatus(): View {
        val box = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(10), dp(10), dp(10), dp(10))
        }
        diagnosticsView = TextView(this).apply {
            text = "Consultando diagnóstico..."
            setTextColor(Color.LTGRAY)
            textSize = 15f
            setPadding(dp(16), dp(16), dp(16), dp(16))
        }
        box.addView(diagnosticsView)
        box.addView(Button(this).apply {
            text = "COMPROBAR DE NUEVO"
            setOnClickListener { checkConnectivity() }
        })
        return box
    }

    private fun buildSettings(): View {
        val box = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(10), dp(10), dp(10), dp(10))
        }

        box.addView(Button(this).apply {
            text = "←  VOLVER AL CHAT"
            isAllCaps = false
            setTextColor(Color.rgb(255, 70, 90))
            setBackground(neonPanel(Color.rgb(30, 18, 22), Color.rgb(255, 70, 90)))
            setOnClickListener { showTab(Tab.CHAT) }
        }, ViewGroup.LayoutParams(-1, ViewGroup.LayoutParams.WRAP_CONTENT))

        box.addView(TextView(this).apply {
            text = "Sesión\\n" + store.sessionId
            setTextColor(Color.LTGRAY)
            textSize = 13f
            setPadding(dp(16), dp(12), dp(16), dp(12))
        })

        box.addView(Button(this).apply {
            text = "📡  Estado y conectividad"
            isAllCaps = false
            setTextColor(Color.LTGRAY)
            setBackground(neonPanel(Color.rgb(11, 11, 15), Color.rgb(58, 58, 66)))
            setOnClickListener { showTab(Tab.STATUS) }
        }, ViewGroup.LayoutParams(-1, ViewGroup.LayoutParams.WRAP_CONTENT))

        box.addView(Button(this).apply {
            text = if (store.voiceEnabled) "VOZ DE RESPUESTA: ACTIVADA" else "VOZ DE RESPUESTA: DESACTIVADA"
            isAllCaps = false
            setTextColor(Color.LTGRAY)
            setBackground(neonPanel(Color.rgb(11, 11, 15), Color.rgb(72, 20, 28)))
            setOnClickListener {
                store.voiceEnabled = !store.voiceEnabled
                text = if (store.voiceEnabled) "VOZ DE RESPUESTA: ACTIVADA" else "VOZ DE RESPUESTA: DESACTIVADA"
                if (!store.voiceEnabled) {
                    textToSpeech?.stop()
                    setVoiceState(AvatarState.IDLE)
                    setVoiceModeUi(false)
                }
            }
        })

        box.addView(TextView(this).apply {
            text = "TONO DE VOZ · " + VoiceTone.fromKey(store.voiceTone).key
            setTextColor(Color.LTGRAY)
            textSize = 14f
            setPadding(dp(16), dp(16), dp(16), dp(6))
        })

        VoiceTone.entries.forEach { tone ->
            box.addView(Button(this).apply {
                text = "Usar " + tone.key + " — " + tone.description
                isAllCaps = false
                setTextColor(Color.LTGRAY)
                setBackground(neonPanel(Color.rgb(10, 10, 13), Color.rgb(54, 54, 62)))
                setOnClickListener {
                    store.voiceTone = tone.key
                    applyVoiceTone()
                    showTab(Tab.SETTINGS)
                }
            })
        }

        val active = Personality.fromKey(store.personality)
        box.addView(TextView(this).apply {
            text = "PERSONALIDAD ACTIVA\\n" + active.key + " — " + active.description
            setTextColor(active.accent)
            textSize = 15f
            setPadding(dp(16), dp(16), dp(16), dp(8))
        })

        Personality.entries.forEach { option ->
            box.addView(Button(this).apply {
                text = "Usar " + option.key
                isAllCaps = false
                setTextColor(option.accent)
                setBackground(neonPanel(Color.rgb(10, 10, 13), option.accent))
                setOnClickListener {
                    selectPersonality(option)
                    showTab(Tab.SETTINGS)
                }
            })
        }

        box.addView(Button(this).apply {
            text = "BORRAR CONVERSACIÓN"
            setTextColor(Color.rgb(220, 120, 130))
            setBackground(neonPanel(Color.rgb(15, 7, 10), Color.rgb(120, 24, 38)))
            setOnClickListener {
                conversation.clear()
                store.clearConversation()
                renderConversation()
            }
        })
        box.addView(Button(this).apply {
            text = "NUEVA SESIÓN"
            setTextColor(Color.LTGRAY)
            setBackground(neonPanel(Color.rgb(10, 10, 13), Color.rgb(58, 58, 66)))
            setOnClickListener { startNewSession() }
        })
        return box
    }

    private fun voicePanelOrNull(): LinearLayout? =
        if (::voicePanel.isInitialized) voicePanel else null

    private fun selectPersonality(personality: Personality) {
        personalitySelectionGeneration++
        // Persist the selection first so every subsequent chat/voice request uses the
        // exact mode chosen by the user, independent of older memory/preferences.
        store.setPersonalityFromUser(personality.key)
        Log.i("DEEP33", "PERSONALITY ACTIVATED: " + personality.key)
        currentPersonalityView.text = "PERSONALIDAD ACTIVA · " + personality.key
        currentPersonalityView.setTextColor(personality.accent)
        currentPersonalityView.setTypeface(currentPersonalityView.typeface, android.graphics.Typeface.BOLD)
        refreshPersonalityButtons()
        if (::sendButton.isInitialized) sendButton.setTextColor(personality.accent)
        if (::cancelButton.isInitialized) applyStopButtonTheme(personality)
        if (::micButton.isInitialized) micButton.setTextColor(personality.accent)
        if (::avatarView.isInitialized) avatarView.setPersonality(personality)
        if (::voicePanel.isInitialized) {
            (voicePanel.background as? GradientDrawable)?.setStroke(dp(1), personality.accent)
        }
        applyVoiceTone(personality)
        syncPreferences()
    }

    private fun applyStopButtonTheme(personality: Personality) {
        if (!::cancelButton.isInitialized) return
        cancelButton.text = STOP_BUTTON_GLYPH
        cancelButton.setTextColor(personality.accent)
        cancelButton.setBackground(
            neonPanel(
                Deep33Theme.RED_SURFACE,
                personality.accent
            )
        )
    }

    private fun applyPersonalityTheme(personality: Personality) {
        currentPersonalityView.text = "PERSONALIDAD ACTIVA · " + personality.key
        currentPersonalityView.setTextColor(personality.accent)
        currentPersonalityView.setTypeface(currentPersonalityView.typeface, android.graphics.Typeface.BOLD)
        refreshPersonalityButtons()
        if (::sendButton.isInitialized) sendButton.setTextColor(personality.accent)
        if (::cancelButton.isInitialized) applyStopButtonTheme(personality)
        if (::micButton.isInitialized) micButton.setTextColor(personality.accent)
        if (::avatarView.isInitialized) avatarView.setPersonality(personality)
        if (::voicePanel.isInitialized) {
            (voicePanel.background as? GradientDrawable)?.setStroke(dp(1), personality.accent)
        }
        // Re-apply the voice profile whenever the active personality is restored from
        // storage/remote memory, not only when the settings button is pressed.
        applyVoiceTone(personality)
    }

    private fun syncPreferences() {
        executor.submit {
            // Preference writes are serialized so an older selection can never finish
            // after a newer selection and overwrite the remote session state.
            synchronized(personalitySyncLock) {
                val personality = Personality.fromKey(store.personality).key
                try {
                    Deep33Api.setPreferences(store.sessionId, personality, memoryProfileId = store.memoryProfileId)
                    Log.i("DEEP33", "PERSONALITY SYNCED: " + personality)
                } catch (e: Exception) {
                    Log.w("DEEP33", "Remote preference sync failed: ${e.javaClass.simpleName}")
                }
            }
        }
    }

    private fun startNewSession() {
        if (generationActive) cancelGeneration()
        saveCurrentSummary()
        stopVoiceInput()
        interruptAssistantSpeech(resumeListening = false)
        store.resetSession()
        conversation.clear()
        showTab(Tab.CHAT)
        renderConversation()
        refreshSidebarHistory()
    }

    private fun openSession(sessionId: String) {
        if (generationActive) cancelGeneration()
        stopVoiceInput()
        interruptAssistantSpeech(resumeListening = false)
        store.activateSession(sessionId)
        conversation.clear()
        conversation.addAll(store.loadMessages())
        showTab(Tab.CHAT)
        renderConversation()
        loadRemoteContext()
    }

    private fun saveCurrentSummary() {
        val title = conversation.firstOrNull { it.role == "user" }
            ?.content
            ?.replace(Regex("\\s+"), " ")
            ?.trim()
            ?.take(60)
            ?.ifBlank { null }
            ?: return
        store.saveChatSummary(title)
    }

    private fun retryPendingMemorySync() {
        if (memorySyncRunning) return
        val pending = store.loadPendingMemorySync() ?: return

        memorySyncRunning = true
        executor.submit {
            try {
                synchronized(memorySyncLock) {
                    val current = store.loadPendingMemorySync()
                    if (current == null || current.requestId != pending.requestId) return@synchronized

                    val payload = org.json.JSONArray(current.messagesJson)
                    Deep33Api.syncMemory(
                        current.sessionId,
                        payload,
                        current.personality,
                        requestId = current.requestId,
                        memoryProfileId = current.memoryProfileId
                    )
                    store.clearPendingMemorySync(current.requestId)
                    Log.i("DEEP33", "REMOTE MEMORY SYNCED: " + current.requestId)
                }
            } catch (e: Exception) {
                Log.w("DEEP33", "Pending memory sync deferred: ${e.javaClass.simpleName}")
            } finally {
                runOnUiThread {
                    memorySyncRunning = false
                    if (store.loadPendingMemorySync() != null && activityVisible) {
                        window.decorView.postDelayed({ retryPendingMemorySync() }, 5_000L)
                    }
                }
            }
        }
    }

    private fun loadRemoteContext() {
        // Capture the session/profile at scheduling time. A previous asynchronous
        // context load must never write old-session data into a newly opened chat.
        val targetSessionId = store.sessionId
        val targetMemoryProfileId = store.memoryProfileId
        val localPersonality = store.personality
        val selectionGeneration = personalitySelectionGeneration
        val localConversation = conversation.takeLast(50)
        executor.submit {
            try {
                val remote = Deep33Api.memoryContext(targetSessionId, targetMemoryProfileId)
                val session = remote.optJSONObject("session")
                val remotePersonality = session?.optString("personality").orEmpty()
                val shouldApplyRemotePersonality =
                    personalitySelectionGeneration == selectionGeneration &&
                        store.personality == localPersonality
                if (remotePersonality.isNotBlank() && shouldApplyRemotePersonality) {
                    store.applyRemotePersonalityIfUnset(remotePersonality)
                }

                val messages = remote.optJSONArray("messages") ?: return@submit
                val remoteMessages = buildList {
                    for (i in 0 until messages.length()) {
                        val item = messages.optJSONObject(i) ?: continue
                        val role = item.optString("role")
                        val content = item.optString("content")
                        if (role in setOf("user", "assistant") && content.isNotBlank()) {
                            add(UiMessage(role, content))
                        }
                    }
                }

                // The user may have opened another chat while this request was in flight.
                // Discard stale results instead of contaminating the active conversation.
                if (store.sessionId != targetSessionId) return@submit

                val merged = mutableListOf<UiMessage>()
                val seen = mutableSetOf<Pair<String, String>>()
                (remoteMessages + localConversation).forEach {
                    if (seen.add(it.role to it.content)) merged.add(it)
                }

                conversation.clear()
                conversation.addAll(merged.takeLast(50))
                store.saveMessages(conversation)

                runOnUiThread {
                    // Remote memory refresh must not rebuild the chat while a pending
                    // generation is active, otherwise the "Pensando..." bubble can be
                    // detached from the active request after foreground recovery.
                    if (generationActive) return@runOnUiThread
                    applyPersonalityTheme(Personality.fromKey(store.personality))
                    renderConversation()
                    refreshSidebarHistory()
                }
            } catch (e: Exception) {
                Log.w("DEEP33", "Remote memory load failed: ${e.javaClass.simpleName}")
            }
        }
    }

    private fun checkConnectivity() {
        updateConnection(ConnectionState.CONNECTING)
        executor.submit {
            try {
                val health = Deep33Api.get("/health", store.sessionId)
                val audit = Deep33Api.get("/v1/connectivity/audit", store.sessionId)
                val diagnostics = Deep33Api.get("/v1/ai/diagnostics", store.sessionId)
                val upstream = audit.optJSONObject("upstream")
                val online = health.optString("status") == "PASS" &&
                    audit.optString("status") == "PASS" &&
                    audit.optString("edge") == "PASS" &&
                    audit.optString("internet") == "PASS" &&
                    upstream?.optBoolean("ready", false) == true &&
                    diagnostics.optBoolean("online", false)
                val gateway = diagnostics.optJSONObject("gateway")
                val provider = gateway?.optString("provider", "") ?: ""
                val model = gateway?.optString("model", "") ?: ""
                val summary = if (online) {
                    "ONLINE\\nEDGE: PASS\\nINTERNET: PASS\\nBACKEND: PASS\\nAI GATEWAY: PASS\\nPROVIDER: " +
                        provider + "\\nMODEL: " + model
                } else {
                    "OFFLINE\\nLa cadena real de DEEP33 no está completamente verificada."
                }
                runOnUiThread {
                    // Connectivity probes must not overwrite a live generation state
                    // while the user has temporarily left and returned to the Activity.
                    if (generationActive) {
                        updateConnection(ConnectionState.CONNECTING)
                    } else {
                        updateConnection(if (online) ConnectionState.ONLINE else ConnectionState.OFFLINE)
                    }
                    if (::diagnosticsView.isInitialized) diagnosticsView.text = summary
                }
            } catch (e: Deep33ApiException) {
                runOnUiThread {
                    val pendingGeneration = generationActive || store.loadPendingTurn() != null
                    if (pendingGeneration) {
                        // A connectivity probe can fail while the real generation is still
                        // recoverable. That is not evidence that the pending AI turn is lost.
                        updateConnection(ConnectionState.CONNECTING)
                        if (::diagnosticsView.isInitialized) {
                            diagnosticsView.text = "PROCESANDO\\nLa solicitud pendiente se conserva mientras se recupera la conexión."
                        }
                    } else {
                        updateConnection(ConnectionState.OFFLINE)
                        if (::diagnosticsView.isInitialized) {
                            diagnosticsView.text = "OFFLINE\\n" + (e.message ?: "Error de conectividad.")
                        }
                    }
                }
            } catch (_: Exception) {
                runOnUiThread {
                    val pendingGeneration = generationActive || store.loadPendingTurn() != null
                    if (pendingGeneration) {
                        updateConnection(ConnectionState.CONNECTING)
                        if (::diagnosticsView.isInitialized) {
                            diagnosticsView.text = "PROCESANDO\\nLa solicitud pendiente se conserva mientras se recupera la conexión."
                        }
                    } else {
                        updateConnection(ConnectionState.OFFLINE)
                        if (::diagnosticsView.isInitialized) {
                            diagnosticsView.text = "OFFLINE\\nError inesperado de conectividad."
                        }
                    }
                }
            }
        }
    }

    private fun sendMessage(textOverride: String? = null) {
        val text = (textOverride ?: input.text.toString()).trim()
        if (text.isEmpty() || generationActive) return

        // Never overwrite a durable request that is waiting for recovery. The pending
        // turn is the single source of truth when the Activity/process disappears.
        val pendingRecovery = store.loadPendingTurn()
        if (pendingRecovery != null) {
            restorePendingTurnIfNeeded()
            return
        }

        // Snapshot the active personality for this turn. The user's next selection must
        // affect the next turn, while this request/voice output remains internally consistent.
        val requestPersonality = Personality.fromKey(store.personality)
        Log.i("DEEP33", "TURN PERSONALITY: " + requestPersonality.key)

        stopVoiceInput()
        if (voiceModeActive) {
            setVoiceModeUi(true)
            setVoiceState(AvatarState.THINKING)
        }

        conversation.add(UiMessage("user", text))
        store.saveMessages(conversation)
        saveCurrentSummary()
        appendBubble("TÚ", text, Color.rgb(12, 34, 27))
        input.setText("")
        refreshSidebarHistory()

        activeBubble = appendBubble("DEEP33", "Pensando...", Color.rgb(42, 12, 18))
        sendButton.isEnabled = false
        input.isEnabled = false
        micButton.isEnabled = false
        cancelButton.visibility = View.VISIBLE
        updateConnection(ConnectionState.CONNECTING)

        val payload = org.json.JSONArray()
        conversation.takeLast(50).forEach {
            payload.put(org.json.JSONObject().put("role", it.role).put("content", it.content))
        }

        val requestId = UUID.randomUUID().toString()
        val idempotencyKey = "chat-" + requestId
        val sessionId = store.sessionId

        // Persist before network execution so Activity/process destruction can recover
        // the exact turn with the same idempotency key.
        store.savePendingTurn(
            PendingTurn(
                sessionId = sessionId,
                requestId = requestId,
                idempotencyKey = idempotencyKey,
                personality = requestPersonality.key,
                payloadJson = payload.toString()
            )
        )
        launchGeneration(payload, sessionId, requestPersonality, requestId, idempotencyKey)
    }

    private fun restorePendingTurnIfNeeded() {
        val state = store.loadGenerationState()
        if (state?.status == GenerationStatus.DONE && state.sessionId == store.sessionId) {
            conversation.clear()
            conversation.addAll(store.loadMessages())
            store.clearGenerationState(state.requestId)
            generationActive = false
            activeRequestId = null
            activeIdempotencyKey = null
            activeBubble = null
            renderConversation()
            refreshSidebarHistory()
            return
        }

        val pending = store.loadPendingTurn() ?: return
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
        activeBubble = appendBubble(
            "DEEP33",
            partial.ifBlank { "Pensando..." },
            Color.rgb(42, 12, 18)
        )
        activeBubble?.tag = partial.ifBlank { "Pensando..." }

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
        generationHandler.removeCallbacks(generationMonitor)
        if (generationActive || store.loadPendingTurn() != null) {
            generationHandler.post(generationMonitor)
        }
    }

    private fun monitorGeneration() {
        if (!generationActive) return

        val state = store.loadGenerationState()
        if (state == null || state.requestId != activeRequestId) {
            if (store.loadPendingTurn() == null) {
                generationActive = false
                activeRequestId = null
                activeIdempotencyKey = null
                cleanupGeneration(false)
                return
            }
            generationHandler.postDelayed(generationMonitor, 250L)
            return
        }

        when (state.status) {
            GenerationStatus.RUNNING -> {
                val output = state.partialOutput.trim()
                if (output.isNotBlank()) {
                    activeBubble?.let {
                        it.tag = output
                        // Streaming text is assistant content too: apply the same
                        // source/citation sanitizer used by the final answer.
                        renderMarkdown(it, output, isAssistant = true)
                    }
                    setVoiceState(AvatarState.SPEAKING)
                }
            }
            GenerationStatus.DONE -> {
                val finalText = state.finalText.trim()
                conversation.clear()
                conversation.addAll(store.loadMessages())
                renderConversation()
                store.clearGenerationState(state.requestId)
                generationActive = false
                activeRequestId = null
                activeIdempotencyKey = null
                activeBubble = null
                cleanupGeneration(true)
                refreshSidebarHistory()
                if (activityVisible && finalText.isNotBlank()) {
                    speakAssistant(finalText, Personality.fromKey(state.personality))
                }
                return
            }
            GenerationStatus.FAILED, GenerationStatus.CANCELLED -> {
                val message = state.error.ifBlank {
                    if (state.status == GenerationStatus.CANCELLED) {
                        "Generación cancelada."
                    } else {
                        "Error de comunicación con DEEP33."
                    }
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
                return
            }
        }
        generationHandler.postDelayed(generationMonitor, 250L)
    }

    private fun launchGeneration(
        payload: org.json.JSONArray,
        sessionId: String,
        requestPersonality: Personality,
        requestId: String,
        idempotencyKey: String
    ) {
        generationActive = true
        activeRequestId = requestId
        activeIdempotencyKey = idempotencyKey
        Deep33GenerationService.start(this, requestId)
        startGenerationMonitor()
    }

    private fun cancelGeneration() {
        if (!generationActive) return
        val requestId = activeRequestId ?: store.loadPendingTurn()?.requestId
        Deep33GenerationService.cancel(this, requestId)
        store.clearPendingTurn(requestId)
        store.clearGenerationState(requestId)
        generationActive = false
        activeRequestId = null
        activeIdempotencyKey = null
        activeBubble?.let {
            it.tag = "Generación cancelada."
            renderMarkdown(it, "Generación cancelada.")
        }
        cleanupGeneration(false)
    }

    private fun cleanupGeneration(success: Boolean) {
        activeBubble = null
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

    private fun updateConnection(state: ConnectionState) {
        val (text, color) = when (state) {
            ConnectionState.CONNECTING -> "PROCESANDO · DEEP33" to Color.rgb(255, 193, 7)
            ConnectionState.ONLINE -> "ONLINE · DEEP33" to Color.rgb(108, 196, 145)
            ConnectionState.OFFLINE -> "OFFLINE · DEEP33" to Color.rgb(255, 80, 80)
        }
        if (::statusView.isInitialized) {
            statusView.text = text
            statusView.setTextColor(color)
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
        val profile = PersonalityVoiceProfile.forPersonality(personality)
        tts.setPitch((tone.pitch * profile.pitchFactor).coerceIn(0.65f, 1.35f))
        tts.setSpeechRate((tone.speechRate * profile.speechRateFactor).coerceIn(0.60f, 1.45f))
    }

    private fun setVoiceModeUi(active: Boolean) {
        if (!::voicePanel.isInitialized) return
        if (voiceModeActive != active) {
            voiceModeGeneration++
        }
        voiceModeActive = active
        if (active) {
            voicePanel.visibility = View.VISIBLE
            voicePanel.gravity = Gravity.CENTER
            voicePanel.layoutParams = LinearLayout.LayoutParams(-1, 0, 1f)
            chatScroll.visibility = View.GONE
            composer.visibility = View.GONE
            voiceStateView.visibility = View.GONE
            avatarView.layoutParams = LinearLayout.LayoutParams(dp(220), dp(220))
        } else {
            voicePanel.visibility = View.GONE
            voicePanel.gravity = Gravity.CENTER_VERTICAL
            voicePanel.layoutParams = LinearLayout.LayoutParams(-1, ViewGroup.LayoutParams.WRAP_CONTENT)
            chatScroll.visibility = View.VISIBLE
            composer.visibility = View.VISIBLE
            voiceStateView.visibility = View.GONE
            avatarView.layoutParams = LinearLayout.LayoutParams(dp(86), dp(86))
        }
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

    private fun speakAssistant(
        text: String,
        personality: Personality = Personality.fromKey(store.personality)
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
            TextToSpeech.QUEUE_FLUSH,
            null,
            utteranceId
        )
    }

    private fun renderConversation() {
        if (!::chatContainer.isInitialized) return
        chatContainer.removeAllViews()
        conversation.takeLast(50).forEach { message ->
            appendBubble(
                if (message.role == "user") "TÚ" else "DEEP33",
                message.content,
                if (message.role == "user") Color.rgb(14, 14, 17) else Color.rgb(18, 7, 11)
            )
        }
        chatContainer.post { scrollToBottom() }
    }

    private fun appendBubble(label: String, content: String, background: Int): TextView {
        val isAssistant = label == "DEEP33"
        val bubble = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(16), dp(13), dp(16), dp(13))
            setBackground(
                GradientDrawable().apply {
                    setColor(background)
                    cornerRadius = dp(18).toFloat()
                    val edge = if (isAssistant) Color.rgb(64, 17, 27) else Deep33Theme.LINE
                    setStroke(dp(1), edge)
                }
            )
        }

        bubble.addView(TextView(this).apply {
            text = label
            setTextColor(if (isAssistant) Personality.fromKey(store.personality).accent else Deep33Theme.TEXT_MUTED)
            textSize = 10.5f
            letterSpacing = 0.10f
            setTypeface(typeface, android.graphics.Typeface.BOLD)
        })

        val contentView = TextView(this).apply {
            tag = content
            textSize = 16f
            letterSpacing = 0.008f
            setTextColor(Deep33Theme.TEXT)
            setPadding(0, dp(6), 0, 0)
            movementMethod = LinkMovementMethod.getInstance()
        }
        bubble.addView(contentView)
        renderMarkdown(contentView, content, isAssistant)

        val params = LinearLayout.LayoutParams(
            if (isAssistant) ViewGroup.LayoutParams.MATCH_PARENT else
                dp(320).coerceAtMost(resources.displayMetrics.widthPixels - dp(70)),
            ViewGroup.LayoutParams.WRAP_CONTENT
        ).apply {
            gravity = if (isAssistant) Gravity.START else Gravity.END
            setMargins(
                if (isAssistant) 0 else dp(34),
                0,
                if (isAssistant) dp(8) else 0,
                dp(12)
            )
        }
        chatContainer.addView(bubble, params)
        bubble.post { scrollToBottom() }
        return contentView
    }

    private fun renderMarkdown(view: TextView, markdown: String, isAssistant: Boolean = false) {
        view.text = MarkdownRenderer.render(
            markdown,
            suppressAssistantSources = isAssistant
        )
        view.movementMethod = LinkMovementMethod.getInstance()
    }

    private fun scrollToBottom() {
        (chatContainer.parent as? ScrollView)?.fullScroll(View.FOCUS_DOWN)
    }

    private fun setVoiceState(state: AvatarState) {
        if (!::avatarView.isInitialized) return
        avatarView.setVoiceState(state)
        voiceStateView.text = when (state) {
            AvatarState.IDLE -> "Modo voz"
            AvatarState.LISTENING -> "Escuchando"
            AvatarState.THINKING -> "Procesando"
            AvatarState.SPEAKING -> "Hablando"
        }
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
                    (recentStop || VoiceConversationPolicy.isStopCommand(recognized) ||
                        !VoiceConversationPolicy.looksLikeTtsEcho(recognized, activeSpeechText))
                ) {
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

                val isExplicitStop = VoiceConversationPolicy.isStopCommand(partial)
                val isLikelyEcho = VoiceConversationPolicy.looksLikeTtsEcho(partial, activeSpeechText)
                if (isExplicitStop || !isLikelyEcho) {
                    if (bargeInCandidateSince == 0L) {
                        bargeInCandidateSince = System.currentTimeMillis()
                    }
                    val elapsed = System.currentTimeMillis() - bargeInCandidateSince
                    if (isExplicitStop || elapsed >= 250L) {
                        interruptAssistantSpeech(resumeListening = voiceModeActive)
                    }
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
        }
    }

    companion object {
        internal const val STOP_BUTTON_GLYPH = "▪️"

        private const val VOICE_PERMISSION_REQUEST = 7001
    }
}
