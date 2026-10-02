package cl.caggrometal.deep33

import android.Manifest
import android.media.AudioAttributes
import android.app.Activity
import android.content.ClipData
import android.content.ClipboardManager
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
import android.widget.EditText
import android.widget.ImageButton
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
    // Deep black surfaces are the visual foundation. Neon is reserved for identity and focus.
    val BG = Color.rgb(3, 3, 3)
    val SURFACE = Color.rgb(7, 7, 7)
    val SURFACE_2 = Color.rgb(11, 11, 11)
    val SURFACE_3 = Color.rgb(16, 16, 16)
    val LINE = Color.rgb(24, 24, 24)
    val LINE_SOFT = Color.rgb(34, 34, 34)
    val TEXT = Color.rgb(242, 242, 242)
    val TEXT_MUTED = Color.rgb(142, 148, 158)
    val RED = Color.rgb(255, 23, 68)
    val RED_NEON = Color.rgb(255, 24, 80)
    val RED_DEEP = Color.rgb(105, 7, 25)
    val RED_SURFACE = Color.rgb(25, 6, 11)
    val GREEN = Color.rgb(57, 255, 20)
    val GREEN_DEEP = Color.rgb(18, 92, 8)
    val GREEN_SURFACE = Color.rgb(6, 25, 4)
    val USER_BUBBLE = Color.rgb(9, 12, 9)
    val ASSISTANT_BUBBLE = Color.rgb(11, 7, 8)
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
    private lateinit var mainHeader: View
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
    private lateinit var diagnosticsView: TextView
    private lateinit var voicePanel: LinearLayout
    private lateinit var avatarView: VoiceAvatarView
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
    // UI-only state: preserves an unsent draft and keeps long chats navigable without
    // inflating the view hierarchy all at once.
    private var chatDraft = ""
    private var visibleHistoryCount = 50
    private var activeRequestId: String? = null
    private var activeIdempotencyKey: String? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        store = SessionStore(this)
        store.migrateNaturalVoiceDefault()
        conversation.addAll(store.loadMessages())

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
            if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.R) {
                val bars = insets.getInsets(android.view.WindowInsets.Type.systemBars())
                view.setPadding(bars.left, bars.top, bars.right, bars.bottom)
            } else {
                @Suppress("DEPRECATION")
                view.setPadding(
                    insets.systemWindowInsetLeft,
                    insets.systemWindowInsetTop,
                    insets.systemWindowInsetRight,
                    insets.systemWindowInsetBottom
                )
            }
            insets
        }

        setContentView(buildRoot())
        showTab(Tab.CHAT)
        renderConversation()
        applyPersonalityTheme(Personality.fromKey(store.personality))
        // Keep startup lightweight: health is enough for the initial status.
        // Full AI/network diagnostics remain manual and never compete with the first chat turn.
        checkHealthFast()
        restorePendingTurnIfNeeded()
        window.decorView.postDelayed({
            if (activityVisible && !generationActive) {
                loadRemoteContext()
                retryPendingMemorySync()
            }
        }, 600L)
    }

    override fun onStart() {
        super.onStart()
        activityVisible = true
        if (wasBackgrounded) {
            store.appBackgrounded = false
            // Tell the foreground service that the Activity is visible again. The service
            // owns the generation and may have switched from SSE to durable HTTP recovery.
            val requestId = activeRequestId ?: store.loadPendingTurn()?.requestId
            Deep33GenerationService.appForeground(this, requestId)

            // Reattach to the durable generation after returning from another app.
            restorePendingTurnIfNeeded()
            retryPendingMemorySync()
            checkHealthFast()
        }
        retryPendingMemorySync()
        startGenerationMonitor()
        wasBackgrounded = false
    }

    override fun onStop() {
        // Activity lifecycle must never cancel the generation. Hand the active request
        // explicitly to the foreground service so it can finish without relying on the UI.
        activityVisible = false
        store.appBackgrounded = true
        val requestId = activeRequestId ?: store.loadPendingTurn()?.requestId
        if (generationActive || requestId != null) {
            Deep33GenerationService.appBackground(this, requestId)
        }
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

    private fun addPressFeedback(view: View) {
        // Deliberately no animated press effect: keep interaction immediate and quiet.
        view.stateListAnimator = null
    }

    private fun iconCircleBackground(fill: Int, stroke: Int): GradientDrawable =
        GradientDrawable().apply {
            shape = GradientDrawable.OVAL
            setColor(fill)
            setStroke(dp(1), stroke)
        }

    private fun styleIconButton(button: ImageButton, tint: Int, fill: Int, stroke: Int) {
        button.imageTintList = android.content.res.ColorStateList.valueOf(tint)
        button.scaleType = android.widget.ImageView.ScaleType.CENTER_INSIDE
        button.setPadding(dp(9), dp(9), dp(9), dp(9))
        button.background = iconCircleBackground(fill, stroke)
        button.stateListAnimator = null
        button.minimumWidth = 0
        button.minimumHeight = 0
    }

    private fun setHeaderStatusStyle(textView: TextView, state: ConnectionState) {
        val (fill, stroke, textColor) = when (state) {
            ConnectionState.CONNECTING ->
                Triple(Color.rgb(19, 16, 7), Color.rgb(88, 68, 19), Color.rgb(231, 190, 68))
            ConnectionState.ONLINE ->
                Triple(Deep33Theme.GREEN_SURFACE, Deep33Theme.GREEN_DEEP, Deep33Theme.GREEN)
            ConnectionState.OFFLINE ->
                Triple(Deep33Theme.RED_SURFACE, Deep33Theme.RED_DEEP, Color.rgb(255, 108, 128))
        }
        textView.setTextColor(textColor)
        textView.setBackground(
            GradientDrawable().apply {
                setColor(fill)
                cornerRadius = dp(12).toFloat()
                setStroke(dp(1), stroke)
            }
        )
    }

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
                    if (!voiceModeActive && dx >= dp(72) && dy <= dp(96)) showSidebar()
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
            setPadding(dp(12), dp(6), dp(12), dp(6))
        }

        val header = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(0, dp(2), 0, dp(10))
        }
        mainHeader = header

        header.addView(ImageButton(this).apply {
            setImageResource(R.drawable.ic_action_menu)
            contentDescription = "Abrir menú"
            styleIconButton(this, Deep33Theme.TEXT_MUTED, Color.TRANSPARENT, Deep33Theme.LINE_SOFT)
            setOnClickListener { toggleSidebar() }
        }, LinearLayout.LayoutParams(dp(42), dp(42)))

        val titleGroup = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER_HORIZONTAL
        }
        titleGroup.addView(TextView(this).apply {
            text = "DEEP33"
            contentDescription = "DEEP33"
            setTextColor(Deep33Theme.TEXT)
            textSize = 26f
            letterSpacing = 0.10f
            setTypeface(android.graphics.Typeface.create("sans-serif", android.graphics.Typeface.BOLD))
        })
        currentPersonalityView = TextView(this).apply {
            textSize = 9.5f
            letterSpacing = 0.10f
            setPadding(0, dp(2), 0, 0)
            setTypeface(typeface, android.graphics.Typeface.BOLD)
            gravity = Gravity.CENTER
            contentDescription = "Personalidad activa"
        }
        titleGroup.addView(currentPersonalityView)
        titleGroup.addView(View(this).apply {
            setBackgroundColor(Deep33Theme.RED_NEON)
            layoutParams = LinearLayout.LayoutParams(dp(22), dp(1)).apply {
                topMargin = dp(6)
            }
        })

        header.addView(
            titleGroup,
            LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        )

        statusView = TextView(this).apply {
            text = "● CONECTANDO"
            textSize = 9f
            letterSpacing = 0.07f
            gravity = Gravity.CENTER
            setTypeface(typeface, android.graphics.Typeface.BOLD)
            setPadding(dp(8), 0, dp(8), 0)
            minWidth = dp(82)
            setHeaderStatusStyle(this, ConnectionState.CONNECTING)
            contentDescription = "Estado de conexión de DEEP33"
        }
        header.addView(statusView, LinearLayout.LayoutParams(dp(88), dp(32)))

        main.addView(header, ViewGroup.LayoutParams(-1, ViewGroup.LayoutParams.WRAP_CONTENT))

        contentFrame = FrameLayout(this)
        main.addView(contentFrame, LinearLayout.LayoutParams(-1, 0, 1f))
        rootFrame.addView(main, FrameLayout.LayoutParams(-1, -1))

        drawerScrim = View(this).apply {
            setBackgroundColor(Color.argb(175, 0, 0, 0))
            visibility = View.GONE
            setOnClickListener { hideSidebar() }
        }
        rootFrame.addView(drawerScrim, FrameLayout.LayoutParams(-1, -1))

        sidebar = buildSidebar()
        rootFrame.addView(
            sidebar,
            FrameLayout.LayoutParams(dp(318), -1).apply { gravity = Gravity.START }
        )
        sidebar.visibility = View.GONE
        return rootFrame
    }
    private fun buildSidebar(): LinearLayout {
        val panel = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(18), dp(20), dp(12), dp(14))
            setBackgroundColor(Color.rgb(5, 5, 6))
            elevation = dp(6).toFloat()
        }

        val header = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        header.addView(TextView(this).apply {
            text = "DEEP33"
            textSize = 20f
            letterSpacing = 0.08f
            setTextColor(Deep33Theme.TEXT)
            setTypeface(typeface, android.graphics.Typeface.BOLD)
        }, LinearLayout.LayoutParams(0, dp(42), 1f))

        header.addView(ImageButton(this).apply {
            setImageResource(R.drawable.ic_action_close)
            contentDescription = "Cerrar menú"
            styleIconButton(this, Deep33Theme.TEXT_MUTED, Color.TRANSPARENT, Color.TRANSPARENT)
            setOnClickListener { hideSidebar() }
        }, LinearLayout.LayoutParams(dp(42), dp(42)))
        panel.addView(header)

        fun row(text: String, accent: Int = Deep33Theme.TEXT, active: Boolean = false, onClick: () -> Unit): TextView =
            TextView(this).apply {
                this.text = text
                textSize = 14f
                setTextColor(accent)
                gravity = Gravity.CENTER_VERTICAL
                includeFontPadding = false
                setPadding(dp(13), 0, dp(10), 0)
                background = neonPanel(
                    if (active) Deep33Theme.RED_SURFACE else Color.TRANSPARENT,
                    if (active) accent else Color.TRANSPARENT,
                    12
                )
                minHeight = dp(46)
                setOnClickListener { onClick() }
            }

        panel.addView(row("＋  NUEVO CHAT", Deep33Theme.TEXT) {
            startNewSession()
            hideSidebar()
        }, LinearLayout.LayoutParams(-1, dp(46)).apply { topMargin = dp(14) })

        panel.addView(row("ESTADO", Deep33Theme.GREEN) {
            showTab(Tab.STATUS)
            hideSidebar()
        }, LinearLayout.LayoutParams(-1, dp(44)).apply { topMargin = dp(4) })

        panel.addView(row("CONFIGURACIÓN", Deep33Theme.RED_NEON) {
            showTab(Tab.SETTINGS)
            hideSidebar()
        }, LinearLayout.LayoutParams(-1, dp(44)).apply { topMargin = dp(2) })

        panel.addView(TextView(this).apply {
            text = "PERSONALIDAD"
            textSize = 10f
            letterSpacing = 0.14f
            setTextColor(Deep33Theme.TEXT_MUTED)
            setPadding(dp(8), dp(18), dp(8), dp(7))
        })

        personalityModesContainer = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
        }
        refreshPersonalityButtons()
        panel.addView(personalityModesContainer)

        panel.addView(TextView(this).apply {
            text = "HISTORIAL"
            textSize = 10f
            letterSpacing = 0.14f
            setTextColor(Deep33Theme.TEXT_MUTED)
            setPadding(dp(8), dp(18), dp(8), dp(7))
        })

        historyContainer = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
        }
        val historyScroll = ScrollView(this).apply {
            isFillViewport = true
            isVerticalScrollBarEnabled = false
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

    private fun personalityButton(option: Personality): TextView =
        TextView(this).apply {
            val active = Personality.fromKey(store.personality) == option
            text = (if (active) "●  " else "○  ") + option.key
            isClickable = true
            textSize = 13.5f
            gravity = Gravity.CENTER_VERTICAL
            includeFontPadding = false
            setPadding(dp(13), 0, dp(10), 0)
            setTextColor(option.accent)
            setTypeface(typeface, if (active) android.graphics.Typeface.BOLD else android.graphics.Typeface.NORMAL)
            contentDescription = if (active) {
                option.description + ". Personalidad activa."
            } else {
                option.description + ". Activar personalidad."
            }
            background = neonPanel(
                if (active) Color.rgb(20, 6, 10) else Color.TRANSPARENT,
                if (active) option.accent else Color.TRANSPARENT,
                12
            )
            minHeight = dp(44)
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
                text = "No hay conversaciones guardadas."
                setTextColor(Deep33Theme.TEXT_MUTED)
                textSize = 12f
                setPadding(dp(8), dp(8), dp(8), dp(8))
            })
            return
        }
        items.forEach { item ->
            historyContainer.addView(TextView(this).apply {
                text = item.title
                textSize = 13f
                maxLines = 2
                ellipsize = android.text.TextUtils.TruncateAt.END
                setTextColor(Deep33Theme.TEXT_MUTED)
                gravity = Gravity.CENTER_VERTICAL
                includeFontPadding = false
                setPadding(dp(13), dp(8), dp(10), dp(8))
                background = neonPanel(Color.TRANSPARENT, Color.TRANSPARENT, 11)
                minHeight = dp(44)
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
        drawerScrim.alpha = 0f
        drawerScrim.animate().alpha(1f).setDuration(150L).start()
        sidebar.visibility = View.VISIBLE
        sidebar.translationX = -dp(318).toFloat()
        sidebar.animate()
            .translationX(0f)
            .setDuration(180L)
            .start()
    }
    private fun hideSidebar() {
        if (sidebar.visibility != View.VISIBLE) return
        drawerScrim.animate().alpha(0f).setDuration(120L).withEndAction {
            drawerScrim.visibility = View.GONE
            drawerScrim.alpha = 1f
        }.start()
        sidebar.animate()
            .translationX(-dp(318).toFloat())
            .setDuration(150L)
            .withEndAction {
                sidebar.visibility = View.GONE
                sidebar.translationX = 0f
            }
            .start()
    }
    private fun showTab(tab: Tab) {
        val previousTab = currentTab
        if (previousTab == Tab.CHAT && ::input.isInitialized) {
            chatDraft = input.text.toString()
        }
        currentTab = tab
        contentFrame.alpha = 1f
        contentFrame.translationY = 0f
        contentFrame.removeAllViews()
        when (tab) {
            Tab.CHAT -> contentFrame.addView(buildChat())
            Tab.STATUS -> contentFrame.addView(buildStatus())
            Tab.SETTINGS -> contentFrame.addView(buildSettings())
        }
    }
    private fun buildChat(): View {
        val box = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(Deep33Theme.BG)
        }

        voicePanel = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER_HORIZONTAL
            setPadding(dp(12), dp(8), dp(12), dp(18))
            visibility = View.GONE
            setBackground(
                GradientDrawable().apply {
                    setColor(Deep33Theme.BG)
                    cornerRadius = dp(24).toFloat()
                    setStroke(dp(1), Personality.fromKey(store.personality).accent)
                }
            )
        }

        val voiceClose = ImageButton(this).apply {
            setImageResource(R.drawable.ic_action_close)
            contentDescription = "Cerrar modo voz"
            styleIconButton(this, Deep33Theme.TEXT_MUTED, Color.TRANSPARENT, Deep33Theme.LINE_SOFT)
            setOnClickListener {
                stopVoiceInput()
                interruptAssistantSpeech(resumeListening = false)
                setVoiceModeUi(false)
            }
        }
        val voiceTop = FrameLayout(this)
        voiceTop.addView(
            voiceClose,
            FrameLayout.LayoutParams(dp(42), dp(42), Gravity.END or Gravity.TOP)
        )
        voicePanel.addView(voiceTop, LinearLayout.LayoutParams(-1, dp(44)))

        avatarView = VoiceAvatarView(this).apply {
            setPersonality(Personality.fromKey(store.personality))
            setVoiceState(AvatarState.IDLE)
            contentDescription = "Avatar de voz de DEEP33"
        }
        val avatarSize = minOf(dp(310), (resources.displayMetrics.widthPixels - dp(28)).coerceAtLeast(dp(220)))
        voicePanel.addView(avatarView, LinearLayout.LayoutParams(avatarSize, avatarSize).apply {
            gravity = Gravity.CENTER
            topMargin = dp(8)
            bottomMargin = dp(10)
        })

        box.addView(voicePanel, LinearLayout.LayoutParams(-1, 0, 1f))

        chatContainer = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(Deep33Theme.BG)
            setPadding(dp(4), dp(8), dp(4), dp(8))
            clipToPadding = false
        }
        chatScroll = ScrollView(this).apply {
            isFillViewport = true
            clipToPadding = false
            isVerticalScrollBarEnabled = false
            addView(chatContainer)
        }
        box.addView(chatScroll, LinearLayout.LayoutParams(-1, 0, 1f))

        fun setComposerBackground(target: View, focused: Boolean) {
            target.background = GradientDrawable().apply {
                setColor(Deep33Theme.SURFACE)
                cornerRadius = dp(18).toFloat()
                setStroke(
                    dp(1),
                    if (focused) Personality.fromKey(store.personality).accent else Deep33Theme.LINE_SOFT
                )
            }
        }

        input = EditText(this).apply {
            hint = "Escribe una idea…"
            setHintTextColor(Deep33Theme.TEXT_MUTED)
            setTextColor(Deep33Theme.TEXT)
            textSize = 16f
            minLines = 1
            maxLines = 5
            gravity = Gravity.CENTER_VERTICAL
            isSingleLine = false
            includeFontPadding = false
            setPadding(dp(16), dp(10), dp(108), dp(10))
            setComposerBackground(this, false)
            setText(chatDraft)
            setSelection(text.length)
        }

        val inputShell = FrameLayout(this).apply {
            setPadding(0, dp(3), 0, dp(3))
        }
        inputShell.addView(input, FrameLayout.LayoutParams(-1, ViewGroup.LayoutParams.WRAP_CONTENT))
        input.setOnFocusChangeListener { _, focused -> setComposerBackground(input, focused) }

        micButton = ImageButton(this).apply {
            contentDescription = "Abrir modo voz"
            setOnClickListener { toggleVoiceInput() }
        }
        styleIconButton(
            micButton,
            Personality.fromKey(store.personality).accent,
            Color.TRANSPARENT,
            Color.TRANSPARENT
        )
        inputShell.addView(
            micButton,
            FrameLayout.LayoutParams(dp(42), dp(42), Gravity.END or Gravity.CENTER_VERTICAL).apply {
                marginEnd = dp(50)
            }
        )

        sendButton = ImageButton(this).apply {
            contentDescription = "Enviar mensaje"
            setOnClickListener { sendMessage() }
        }
        styleIconButton(
            sendButton,
            Color.WHITE,
            Personality.fromKey(store.personality).accent,
            Personality.fromKey(store.personality).accent
        )
        inputShell.addView(
            sendButton,
            FrameLayout.LayoutParams(dp(42), dp(42), Gravity.END or Gravity.CENTER_VERTICAL).apply {
                marginEnd = dp(4)
            }
        )

        cancelButton = ImageButton(this).apply {
            contentDescription = "Detener generación"
            visibility = View.GONE
            setOnClickListener { cancelGeneration() }
        }
        applyStopButtonTheme(Personality.fromKey(store.personality))

        composer = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.BOTTOM
            setPadding(0, dp(5), 0, dp(3))
            addView(inputShell, LinearLayout.LayoutParams(0, dp(62), 1f).apply {
                setMargins(0, 0, dp(5), 0)
            })
            addView(cancelButton, LinearLayout.LayoutParams(dp(42), dp(42)).apply {
                setMargins(0, dp(4), dp(1), 0)
            })
        }
        box.addView(composer)
        return box
    }
    private fun buildStatus(): View {
        val scroll = ScrollView(this).apply {
            isFillViewport = true
            isVerticalScrollBarEnabled = false
        }
        val box = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(6), dp(12), dp(6), dp(24))
        }

        box.addView(TextView(this).apply {
            text = "ESTADO"
            textSize = 23f
            letterSpacing = 0.06f
            setTextColor(Deep33Theme.TEXT)
            setTypeface(typeface, android.graphics.Typeface.BOLD)
            setPadding(dp(8), dp(5), dp(8), dp(15))
        })

        val card = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(15), dp(14), dp(15), dp(15))
            setBackground(neonPanel(Deep33Theme.SURFACE, Deep33Theme.LINE_SOFT, 16))
        }

        val titleRow = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        titleRow.addView(TextView(this).apply {
            text = "CONECTIVIDAD REAL"
            textSize = 10.5f
            letterSpacing = 0.12f
            setTextColor(Deep33Theme.TEXT_MUTED)
            setTypeface(typeface, android.graphics.Typeface.BOLD)
        }, LinearLayout.LayoutParams(0, dp(28), 1f))
        titleRow.addView(View(this).apply {
            setBackgroundColor(Deep33Theme.GREEN)
            layoutParams = LinearLayout.LayoutParams(dp(7), dp(7))
        })
        card.addView(titleRow)

        diagnosticsView = TextView(this).apply {
            text = "Consultando diagnóstico…"
            textSize = 14f
            setTextColor(Deep33Theme.TEXT)
            setPadding(0, dp(12), 0, dp(14))
            includeFontPadding = false
        }
        card.addView(diagnosticsView)

        card.addView(TextView(this).apply {
            text = "El diagnóstico completo se ejecuta solo desde aquí."
            textSize = 11.5f
            setTextColor(Deep33Theme.TEXT_MUTED)
            setPadding(0, 0, 0, dp(11))
        })

        card.addView(TextView(this).apply {
            text = "COMPROBAR DE NUEVO"
            textSize = 12f
            letterSpacing = 0.08f
            gravity = Gravity.CENTER
            setTextColor(Deep33Theme.RED_NEON)
            setBackground(neonPanel(Deep33Theme.RED_SURFACE, Deep33Theme.RED_DEEP, 12))
            minHeight = dp(44)
            setOnClickListener { checkConnectivity() }
        }, LinearLayout.LayoutParams(-1, dp(44)))

        box.addView(card)
        scroll.addView(box)
        return scroll
    }
    private fun buildSettings(): View {
        val scroll = ScrollView(this).apply {
            isFillViewport = true
            isVerticalScrollBarEnabled = false
        }
        val box = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(6), dp(12), dp(6), dp(24))
        }

        box.addView(TextView(this).apply {
            text = "AJUSTES"
            textSize = 23f
            letterSpacing = 0.06f
            setTextColor(Deep33Theme.TEXT)
            setTypeface(typeface, android.graphics.Typeface.BOLD)
            setPadding(dp(8), dp(5), dp(8), dp(15))
        })

        fun section(title: String, body: View, accent: Int = Deep33Theme.RED_NEON): View {
            val card = LinearLayout(this).apply {
                orientation = LinearLayout.VERTICAL
                setPadding(dp(14), dp(13), dp(14), dp(14))
                setBackground(neonPanel(Deep33Theme.SURFACE, Deep33Theme.LINE_SOFT, 16))
            }
            card.addView(TextView(this).apply {
                text = title
                textSize = 10.5f
                letterSpacing = 0.13f
                setTextColor(accent)
                setTypeface(typeface, android.graphics.Typeface.BOLD)
                setPadding(0, 0, 0, dp(10))
            })
            card.addView(body)
            return card
        }

        fun row(
            title: String,
            subtitle: String? = null,
            accent: Int = Deep33Theme.TEXT,
            active: Boolean = false,
            onClick: (() -> Unit)? = null
        ): TextView = TextView(this).apply {
            text = if (subtitle.isNullOrBlank()) title else "$title\n$subtitle"
            textSize = 13.5f
            setTextColor(if (active) accent else Deep33Theme.TEXT)
            includeFontPadding = false
            gravity = Gravity.CENTER_VERTICAL
            setPadding(dp(12), dp(9), dp(10), dp(9))
            background = neonPanel(
                if (active) Color.rgb(18, 6, 9) else Color.TRANSPARENT,
                if (active) accent else Color.TRANSPARENT,
                12
            )
            minHeight = if (subtitle.isNullOrBlank()) dp(44) else dp(52)
            isClickable = onClick != null
            onClick?.let { setOnClickListener { it() } }
        }

        val voiceBody = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
        }
        voiceBody.addView(
            row(
                title = if (store.voiceEnabled) "VOZ DE RESPUESTA · ACTIVA" else "VOZ DE RESPUESTA · INACTIVA",
                subtitle = "La voz de respuesta no convierte el chat en modo voz.",
                accent = if (store.voiceEnabled) Deep33Theme.GREEN else Deep33Theme.TEXT_MUTED,
                active = store.voiceEnabled
            ) {
                store.voiceEnabled = !store.voiceEnabled
                if (!store.voiceEnabled) {
                    textToSpeech?.stop()
                    setVoiceState(AvatarState.IDLE)
                    setVoiceModeUi(false)
                }
                showTab(Tab.SETTINGS)
            }
        )
        voiceBody.addView(TextView(this).apply {
            text = "TONO"
            textSize = 9.5f
            letterSpacing = 0.14f
            setTextColor(Deep33Theme.TEXT_MUTED)
            setPadding(dp(4), dp(12), dp(4), dp(5))
        })

        VoiceTone.entries.forEach { tone ->
            val selected = tone.key == VoiceTone.fromKey(store.voiceTone).key
            voiceBody.addView(
                row(
                    title = tone.key,
                    subtitle = tone.description,
                    accent = Deep33Theme.RED_NEON,
                    active = selected
                ) {
                    store.voiceTone = tone.key
                    applyVoiceTone()
                    showTab(Tab.SETTINGS)
                }
            )
        }

        box.addView(section("VOZ", voiceBody), LinearLayout.LayoutParams(-1, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
            setMargins(dp(2), 0, dp(2), dp(9))
        })

        val active = Personality.fromKey(store.personality)
        val personalityBody = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
        }
        personalityBody.addView(
            row(
                title = active.key,
                subtitle = active.description,
                accent = active.accent,
                active = true
            )
        )
        Personality.entries.filter { it != active }.forEach { option ->
            personalityBody.addView(
                row(
                    title = option.key,
                    subtitle = option.description,
                    accent = option.accent,
                    active = false
                ) {
                    selectPersonality(option)
                    showTab(Tab.SETTINGS)
                }
            )
        }
        box.addView(section("PERSONALIDAD", personalityBody, active.accent), LinearLayout.LayoutParams(-1, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
            setMargins(dp(2), 0, dp(2), dp(9))
        })

        val sessionBody = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
        }
        sessionBody.addView(
            row(
                title = "BORRAR CONVERSACIÓN",
                subtitle = "Elimina el contenido local de esta conversación.",
                accent = Deep33Theme.RED_NEON,
                active = false
            ) {
                conversation.clear()
                visibleHistoryCount = 50
                store.clearConversation()
                renderConversation()
            }
        )
        sessionBody.addView(
            row(
                title = "NUEVA SESIÓN",
                subtitle = "Abre una conversación limpia sin mostrar identificadores técnicos.",
                accent = Deep33Theme.TEXT,
                active = false
            ) {
                startNewSession()
            }
        )
        box.addView(section("SESIÓN", sessionBody), LinearLayout.LayoutParams(-1, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
            setMargins(dp(2), 0, dp(2), 0)
        })

        scroll.addView(box)
        return scroll
    }
    private fun selectPersonality(personality: Personality) {
        personalitySelectionGeneration++
        // Persist before repainting so every subsequent request uses the exact selection.
        store.setPersonalityFromUser(personality.key)
        Log.i("DEEP33", "PERSONALITY ACTIVATED: " + personality.key)
        currentPersonalityView.text = personality.key
        currentPersonalityView.setTextColor(personality.accent)
        currentPersonalityView.contentDescription = "Personalidad activa: " + personality.key
        currentPersonalityView.setTypeface(currentPersonalityView.typeface, android.graphics.Typeface.BOLD)
        refreshPersonalityButtons()
        if (::sendButton.isInitialized) {
            sendButton.imageTintList = android.content.res.ColorStateList.valueOf(Color.WHITE)
            sendButton.background = iconCircleBackground(personality.accent, personality.accent)
        }
        if (::cancelButton.isInitialized) applyStopButtonTheme(personality)
        if (::micButton.isInitialized) {
            micButton.imageTintList = android.content.res.ColorStateList.valueOf(personality.accent)
            micButton.background = iconCircleBackground(Color.TRANSPARENT, Color.TRANSPARENT)
        }
        if (::avatarView.isInitialized) avatarView.setPersonality(personality)
        if (::voicePanel.isInitialized) {
            (voicePanel.background as? GradientDrawable)?.setStroke(dp(1), personality.accent)
        }
        applyVoiceTone(personality)
        syncPreferences()
    }
    private fun applyStopButtonTheme(personality: Personality) {
        if (!::cancelButton.isInitialized) return
        cancelButton.setImageResource(R.drawable.ic_action_stop)
        styleIconButton(
            cancelButton,
            personality.accent,
            Color.rgb(31, 7, 13),
            personality.accent
        )
    }

    private fun applyPersonalityTheme(personality: Personality) {
        currentPersonalityView.text = personality.key
        currentPersonalityView.setTextColor(personality.accent)
        currentPersonalityView.contentDescription = "Personalidad activa: " + personality.key
        currentPersonalityView.setTypeface(currentPersonalityView.typeface, android.graphics.Typeface.BOLD)
        refreshPersonalityButtons()
        if (::sendButton.isInitialized) {
            sendButton.imageTintList = android.content.res.ColorStateList.valueOf(Color.WHITE)
            sendButton.background = iconCircleBackground(personality.accent, personality.accent)
        }
        if (::cancelButton.isInitialized) applyStopButtonTheme(personality)
        if (::micButton.isInitialized) {
            micButton.imageTintList = android.content.res.ColorStateList.valueOf(personality.accent)
            micButton.background = iconCircleBackground(Color.TRANSPARENT, Color.TRANSPARENT)
        }
        if (::avatarView.isInitialized) avatarView.setPersonality(personality)
        if (::voicePanel.isInitialized) {
            (voicePanel.background as? GradientDrawable)?.setStroke(dp(1), personality.accent)
        }
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
        visibleHistoryCount = 50
        chatDraft = ""
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
        visibleHistoryCount = 50
        chatDraft = ""
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

                if (store.sessionId != targetSessionId) return@submit

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

                val merged = mutableListOf<UiMessage>()
                val seen = mutableSetOf<Pair<String, String>>()
                (remoteMessages + localConversation).forEach {
                    if (seen.add(it.role to it.content)) merged.add(it)
                }
                val mergedSnapshot = merged.takeLast(50)

                runOnUiThread {
                    // Re-check the session on the UI thread immediately before mutating
                    // the active conversation. This closes the final race between the
                    // worker finishing and the user opening a different chat.
                    if (store.sessionId != targetSessionId) return@runOnUiThread

                    conversation.clear()
                    conversation.addAll(mergedSnapshot)
                    store.saveMessages(conversation)

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

    private fun checkHealthFast() {
        updateConnection(ConnectionState.CONNECTING)
        executor.submit {
            try {
                val health = Deep33Api.get("/health", store.sessionId)
                val online = health.optString("status") == "PASS"
                runOnUiThread {
                    if (generationActive) {
                        updateConnection(ConnectionState.CONNECTING)
                    } else {
                        updateConnection(if (online) ConnectionState.ONLINE else ConnectionState.OFFLINE)
                    }
                    if (::diagnosticsView.isInitialized && currentTab == Tab.STATUS) {
                        diagnosticsView.text = if (online) {
                            "ONLINE\nEDGE: PASS\nHEALTH: PASS\nDiagnóstico profundo: disponible manualmente."
                        } else {
                            "OFFLINE\nDEEP33 no respondió al chequeo rápido."
                        }
                    }
                }
            } catch (e: Exception) {
                runOnUiThread {
                    if (generationActive || store.loadPendingTurn() != null) {
                        updateConnection(ConnectionState.CONNECTING)
                    } else {
                        updateConnection(ConnectionState.OFFLINE)
                    }
                    if (::diagnosticsView.isInitialized && currentTab == Tab.STATUS) {
                        diagnosticsView.text = "OFFLINE\n" + (e.message ?: "No se pudo comprobar DEEP33.")
                    }
                }
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
        appendBubble("TÚ", text, Deep33Theme.USER_BUBBLE)
        input.setText("")
        chatDraft = ""
        refreshSidebarHistory()

        activeBubble = appendBubble("DEEP33", "Pensando...", Color.rgb(42, 12, 18))
        sendButton.isEnabled = false
        input.isEnabled = false
        micButton.isEnabled = false
        cancelButton.visibility = View.VISIBLE
        updateConnection(ConnectionState.CONNECTING)

        val payload = buildModelPayload()

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
        lastRenderedGenerationOutput = ""
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
            partial.ifBlank { "Pensando..." },
            Deep33Theme.ASSISTANT_BUBBLE
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

    private fun buildModelPayload(): org.json.JSONArray {
        val selected = GenerationPerformancePolicy.selectModelContext(conversation)
        val payload = org.json.JSONArray()
        var chars = 0
        selected.forEach { message ->
            chars += message.content.length
            payload.put(
                org.json.JSONObject()
                    .put("role", message.role)
                    .put("content", message.content)
            )
        }
        Log.i(
            "DEEP33",
            "MODEL_CONTEXT messages=${selected.size} chars=${chars}"
        )
        return payload
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
            generationHandler.postDelayed(generationMonitor, 150L)
            return
        }

        when (state.status) {
            GenerationStatus.RUNNING -> {
                val output = state.partialOutput.trim()
                if (output.isNotBlank()) {
                    if (output != lastRenderedGenerationOutput) {
                        activeBubble?.let {
                            it.tag = output
                            // Streaming text is assistant content too: apply the same
                            // source/citation sanitizer used by the final answer.
                            renderMarkdown(it, output, isAssistant = true)
                        }
                        lastRenderedGenerationOutput = output
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
        generationHandler.postDelayed(generationMonitor, 150L)
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
        lastRenderedGenerationOutput = ""
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

    private fun neonPanel(fill: Int, stroke: Int, radiusDp: Int = 14): GradientDrawable =
        GradientDrawable().apply {
            setColor(fill)
            cornerRadius = dp(radiusDp).toFloat()
            setStroke(dp(1), stroke)
        }

    private fun updateConnection(state: ConnectionState) {
        val text = when (state) {
            ConnectionState.CONNECTING -> "● CONECTANDO"
            ConnectionState.ONLINE -> "● ONLINE"
            ConnectionState.OFFLINE -> "● OFFLINE"
        }
        if (::statusView.isInitialized) {
            statusView.text = text
            setHeaderStatusStyle(statusView, state)
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
            mainHeader.visibility = View.GONE
            voicePanel.visibility = View.VISIBLE
            voicePanel.gravity = Gravity.CENTER
            voicePanel.layoutParams = LinearLayout.LayoutParams(-1, 0, 1f)
            chatScroll.visibility = View.GONE
            composer.visibility = View.GONE
            val avatarSize = minOf(
                dp(310),
                (resources.displayMetrics.widthPixels - dp(28)).coerceAtLeast(dp(220))
            )
            avatarView.layoutParams = LinearLayout.LayoutParams(avatarSize, avatarSize).apply {
                gravity = Gravity.CENTER
            }
        } else {
            mainHeader.visibility = View.VISIBLE
            voicePanel.visibility = View.GONE
            voicePanel.gravity = Gravity.CENTER
            voicePanel.layoutParams = LinearLayout.LayoutParams(-1, ViewGroup.LayoutParams.WRAP_CONTENT)
            chatScroll.visibility = View.VISIBLE
            composer.visibility = View.VISIBLE
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

    private fun buildEmptyState(): View {
        val panel = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER
            setPadding(dp(26), dp(24), dp(26), dp(26))
        }
        panel.addView(TextView(this).apply {
            text = "DEEP33"
            textSize = 27f
            letterSpacing = 0.15f
            setTextColor(Deep33Theme.TEXT)
            setTypeface(typeface, android.graphics.Typeface.BOLD)
            gravity = Gravity.CENTER
        })
        panel.addView(View(this).apply {
            setBackgroundColor(Deep33Theme.RED_NEON)
            layoutParams = LinearLayout.LayoutParams(dp(28), dp(1)).apply {
                topMargin = dp(9)
                bottomMargin = dp(15)
            }
        })
        panel.addView(TextView(this).apply {
            text = "Intercambia ideas. Explora. Cuestiona."
            textSize = 14.5f
            setTextColor(Deep33Theme.TEXT)
            gravity = Gravity.CENTER
        })
        panel.addView(TextView(this).apply {
            text = "Escribe una idea para comenzar."
            textSize = 12.5f
            setTextColor(Deep33Theme.TEXT_MUTED)
            gravity = Gravity.CENTER
            setPadding(0, dp(9), 0, 0)
        })
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
            val start = (conversation.size - visibleHistoryCount).coerceAtLeast(0)
            if (start > 0) {
                val older = TextView(this).apply {
                    text = "CARGAR MENSAJES ANTERIORES"
                    contentDescription = "Cargar mensajes anteriores"
                    textSize = 10.5f
                    letterSpacing = 0.08f
                    setTextColor(Deep33Theme.TEXT_MUTED)
                    gravity = Gravity.CENTER
                    setPadding(dp(12), dp(10), dp(12), dp(10))
                    setOnClickListener {
                        visibleHistoryCount = (visibleHistoryCount + 50).coerceAtMost(conversation.size)
                        renderConversation()
                    }
                }
                chatContainer.addView(older, LinearLayout.LayoutParams(-1, dp(40)))
            }

            conversation.drop(start).forEach { message ->
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
        val accent = if (isAssistant) Deep33Theme.RED_NEON else Deep33Theme.GREEN
        val bubble = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            tag = if (isAssistant) "chat-bubble-assistant" else "chat-bubble-user"
            setPadding(dp(14), dp(11), dp(14), dp(11))
            setBackground(
                GradientDrawable().apply {
                    setColor(background)
                    cornerRadius = dp(17).toFloat()
                    setStroke(dp(1), accent)
                }
            )
        }

        val headerRow = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        headerRow.addView(TextView(this).apply {
            text = label
            setTextColor(accent)
            textSize = 9.5f
            letterSpacing = 0.10f
            setTypeface(typeface, android.graphics.Typeface.BOLD)
        })
        headerRow.addView(View(this).apply {
            setBackgroundColor(accent)
            alpha = 0.58f
            layoutParams = LinearLayout.LayoutParams(dp(14), dp(1)).apply {
                leftMargin = dp(7)
            }
        })
        bubble.addView(headerRow)

        val contentView = TextView(this).apply {
            tag = content
            textSize = 16f
            letterSpacing = 0.004f
            includeFontPadding = false
            setTextColor(Deep33Theme.TEXT)
            setPadding(0, dp(7), 0, 0)
            movementMethod = LinkMovementMethod.getInstance()
            maxWidth = (resources.displayMetrics.widthPixels *
                if (isAssistant) 0.90f else 0.82f).roundToInt().coerceAtLeast(dp(120))
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
            setMargins(
                if (isAssistant) dp(1) else dp(34),
                0,
                if (isAssistant) dp(34) else dp(1),
                dp(9)
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
            setPadding(0, dp(4), 0, 0)
        }

        val normalTint = Color.rgb(124, 130, 140)
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
                setPadding(dp(7), dp(7), dp(7), dp(7))
                background = iconCircleBackground(Color.TRANSPARENT, Color.TRANSPARENT)
                contentDescription = description
                minimumWidth = 0
                minimumHeight = 0
                layoutParams = LinearLayout.LayoutParams(dp(34), dp(34)).apply {
                    rightMargin = dp(2)
                }
                addPressFeedback(this)
                setOnClickListener { onClick(this) }
            }

        row.addView(actionButton(
            R.drawable.ic_action_copy,
            "Copiar respuesta"
        ) { button ->
            val clipboard = getSystemService(CLIPBOARD_SERVICE) as ClipboardManager
            clipboard.setPrimaryClip(ClipData.newPlainText("DEEP33", content))
            button.imageTintList = android.content.res.ColorStateList.valueOf(activeTint)
            button.postDelayed({
                button.imageTintList = android.content.res.ColorStateList.valueOf(normalTint)
            }, 650L)
        })

        row.addView(actionButton(
            R.drawable.ic_action_volume,
            "Leer respuesta en voz alta"
        ) { button ->
            speakAssistant(content, Personality.fromKey(store.personality))
            button.imageTintList = android.content.res.ColorStateList.valueOf(activeTint)
            button.postDelayed({
                button.imageTintList = android.content.res.ColorStateList.valueOf(normalTint)
            }, 900L)
        })

        val likeButton = actionButton(R.drawable.ic_action_like, "Respuesta útil") { button ->
            button.imageTintList = android.content.res.ColorStateList.valueOf(activeTint)
            dislikeButtonTint(normalTint, button, null)
        }
        row.addView(likeButton)

        val dislikeButton = actionButton(R.drawable.ic_action_dislike, "Respuesta no útil") { button ->
            button.imageTintList = android.content.res.ColorStateList.valueOf(activeTint)
            dislikeButtonTint(normalTint, null, button)
        }
        row.addView(dislikeButton)

        row.addView(actionButton(
            R.drawable.ic_action_retry,
            "Intentar de nuevo"
        ) {
            regenerateLastResponse()
        })

        bubble.addView(row, LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, dp(34)
        ))
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

    private fun regenerateLastResponse() {
        if (generationActive) return

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
        // Voice mode communicates state visually through the minimal avatar only.
        avatarView.setVoiceState(state)
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
         private const val VOICE_PERMISSION_REQUEST = 7001
    }
}





