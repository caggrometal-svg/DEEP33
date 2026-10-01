package cl.caggrometal.deep33

import android.Manifest
import android.media.AudioAttributes
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
    // Monotonic UI revision: remote-memory loads may only merge into the exact
    // conversation state they were started against. This prevents stale async work
    // from restoring an older chat over a newer local conversation.
    private var conversationRevision = 0L
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
        // Do not let an already queued UI poll mutate a stopped Activity. The durable
        // generation state remains owned by the foreground service and is re-attached
        // when the Activity returns to the foreground.
        generationHandler.removeCallbacks(generationMonitor)

        // Commit the visible conversation before the Activity leaves the foreground.
        // This is local persistence only; the network path is intentionally untouched.
        if (::store.isInitialized) {
            store.saveMessages(conversation, durable = true)
        }
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
        view.setOnTouchListener { touched, event ->
            when (event.actionMasked) {
                android.view.MotionEvent.ACTION_DOWN ->
                    touched.animate().scaleX(0.975f).scaleY(0.975f).setDuration(70L).start()
                android.view.MotionEvent.ACTION_UP,
                android.view.MotionEvent.ACTION_CANCEL ->
                    touched.animate().scaleX(1f).scaleY(1f).setDuration(110L).start()
            }
            false
        }
    }

    private fun setHeaderStatusStyle(textView: TextView, state: ConnectionState) {
        val (fill, stroke, textColor) = when (state) {
            ConnectionState.CONNECTING ->
                Triple(Color.rgb(35, 27, 8), Color.rgb(112, 84, 20), Color.rgb(244, 198, 74))
            ConnectionState.ONLINE ->
                Triple(Color.rgb(8, 32, 18), Color.rgb(24, 103, 58), Color.rgb(111, 226, 157))
            ConnectionState.OFFLINE ->
                Triple(Color.rgb(39, 9, 14), Color.rgb(116, 26, 42), Color.rgb(255, 102, 121))
        }
        textView.setTextColor(textColor)
        textView.setBackground(
            GradientDrawable().apply {
                setColor(fill)
                cornerRadius = dp(18).toFloat()
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
            textSize = 17f
            minWidth = 0
            minHeight = 0
            setPadding(0, 0, 0, 0)
            gravity = Gravity.CENTER
            isAllCaps = false
            setTextColor(Deep33Theme.RED_DEEP)
            setBackground(
                GradientDrawable().apply {
                    shape = GradientDrawable.OVAL
                    setColor(Color.rgb(18, 3, 7))
                    setStroke(dp(1), Deep33Theme.RED_DEEP)
                }
            )
            setLayerType(View.LAYER_TYPE_SOFTWARE, null)
            paint.setShadowLayer(dp(4).toFloat(), 0f, 0f, Deep33Theme.RED_DEEP)
            elevation = dp(2).toFloat()
            stateListAnimator = null
            setOnClickListener { toggleSidebar() }
            addPressFeedback(this)
        }, LinearLayout.LayoutParams(dp(38), dp(38)))

        statusView = TextView(this).apply {
            text = "PROCESANDO · DEEP33"
            textSize = 11f
            setPadding(0, dp(2), 0, 0)
        }

        val titleGroup = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER_VERTICAL
        }
        currentPersonalityView = TextView(this).apply {
            text = "DEEP33"
            textSize = 12.5f
            letterSpacing = 0.10f
            singleLine = true
            setPadding(dp(12), dp(6), dp(12), dp(6))
            setTypeface(typeface, android.graphics.Typeface.BOLD)
            setTextColor(Deep33Theme.RED_DEEP)
            setBackground(
                GradientDrawable().apply {
                    setColor(Color.rgb(18, 3, 7))
                    cornerRadius = dp(16).toFloat()
                    setStroke(dp(1), Deep33Theme.RED_DEEP)
                }
            )
            setLayerType(View.LAYER_TYPE_SOFTWARE, null)
            paint.setShadowLayer(dp(5).toFloat(), 0f, 0f, Deep33Theme.RED_DEEP)
            contentDescription = "Personalidad activa de DEEP33"
        }

        titleGroup.addView(
            currentPersonalityView,
            LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            )
        )

        header.addView(
            titleGroup,
            LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply {
                leftMargin = dp(8)
            }
        )
        statusView = TextView(this).apply {
            text = "● CONECTANDO"
            textSize = 9.5f
            letterSpacing = 0.08f
            gravity = Gravity.CENTER
            setTypeface(typeface, android.graphics.Typeface.BOLD)
            setPadding(dp(10), 0, dp(10), 0)
            minWidth = dp(94)
            setHeaderStatusStyle(this, ConnectionState.CONNECTING)
            contentDescription = "Estado de conexión de DEEP33"
        }
        header.addView(statusView, LinearLayout.LayoutParams(dp(104), dp(34)))

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
            isAllCaps = false
            setTextColor(Deep33Theme.TEXT_MUTED)
            setBackground(neonPanel(Deep33Theme.SURFACE_2, Deep33Theme.LINE_SOFT))
            setOnClickListener { hideSidebar() }
            addPressFeedback(this)
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
            addPressFeedback(this)
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
            addPressFeedback(this)
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
                "●  " + option.key + " · ACTIVA"
            } else {
                "○  " + option.key
            }
            isAllCaps = false
            gravity = Gravity.START or Gravity.CENTER_VERTICAL
            minHeight = dp(54)
            setTextColor(option.accent)
            setTypeface(typeface, if (active) android.graphics.Typeface.BOLD else android.graphics.Typeface.NORMAL)
            contentDescription = if (active) {
                option.description + ". Personalidad activa."
            } else {
                option.description + ". Activar personalidad."
            }
            setBackground(
                neonPanel(
                    if (active) Color.rgb(30, 8, 13) else Deep33Theme.SURFACE,
                    if (active) option.accent else Deep33Theme.LINE_SOFT
                )
            )
            setOnClickListener {
                selectPersonality(option)
                hideSidebar()
            }
            addPressFeedback(this)
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
                setTextColor(Deep33Theme.TEXT_MUTED)
                setBackground(neonPanel(Color.rgb(9, 9, 11), Deep33Theme.LINE))
                setOnClickListener {
                    openSession(item.sessionId)
                    hideSidebar()
                }
                addPressFeedback(this)
            })
        }
    }

    private fun toggleSidebar() {
        if (sidebar.visibility == View.VISIBLE) hideSidebar() else showSidebar()
    }

    private fun showSidebar() {
        refreshSidebarHistory()
        drawerScrim.animate().cancel()
        sidebar.animate().cancel()
        drawerScrim.alpha = 0f
        drawerScrim.visibility = View.VISIBLE
        sidebar.visibility = View.VISIBLE
        sidebar.translationX = -dp(322).toFloat()
        drawerScrim.animate().alpha(1f).setDuration(180L).start()
        sidebar.animate()
            .translationX(0f)
            .setDuration(240L)
            .setInterpolator(android.view.animation.DecelerateInterpolator())
            .start()
    }

    private fun hideSidebar() {
        if (sidebar.visibility != View.VISIBLE) return
        drawerScrim.animate().cancel()
        sidebar.animate().cancel()
        drawerScrim.animate().alpha(0f).setDuration(150L).start()
        sidebar.animate()
            .translationX(-dp(322).toFloat())
            .setDuration(190L)
            .setInterpolator(android.view.animation.AccelerateInterpolator())
            .withEndAction {
                sidebar.visibility = View.GONE
                sidebar.translationX = 0f
                drawerScrim.visibility = View.GONE
                drawerScrim.alpha = 1f
            }
            .start()
    }

    private fun showTab(tab: Tab) {
        currentTab = tab
        contentFrame.animate().cancel()
        contentFrame.alpha = 0f
        contentFrame.translationY = dp(8).toFloat()
        contentFrame.removeAllViews()
        when (tab) {
            Tab.CHAT -> contentFrame.addView(buildChat())
            Tab.STATUS -> contentFrame.addView(buildStatus())
            Tab.SETTINGS -> contentFrame.addView(buildSettings())
        }
        contentFrame.animate()
            .alpha(1f)
            .translationY(0f)
            .setDuration(170L)
            .setInterpolator(android.view.animation.DecelerateInterpolator())
            .start()
    }

    private fun buildChat(): View {
        val box = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(Color.BLACK)
        }

        voicePanel = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER_HORIZONTAL
            setPadding(dp(18), dp(16), dp(18), dp(22))
            visibility = View.GONE
            setBackground(
                GradientDrawable().apply {
                    setColor(Deep33Theme.BG)
                    cornerRadius = dp(22).toFloat()
                    setStroke(dp(1), Personality.fromKey(store.personality).accent)
                }
            )
        }

        // Voice mode is intentionally audio-first: no title, status text, or transcript.
        // The face carries only personality eyebrows/expression; the close control is icon-only.
        voicePanel.addView(Button(this).apply {
            text = "×"
            contentDescription = "Cerrar modo voz"
            textSize = 24f
            isAllCaps = false
            minWidth = dp(46)
            minHeight = dp(46)
            gravity = Gravity.CENTER
            setTextColor(Deep33Theme.TEXT_MUTED)
            setBackground(neonPanel(Deep33Theme.SURFACE_2, Deep33Theme.LINE_SOFT))
            setOnClickListener {
                stopVoiceInput()
                interruptAssistantSpeech(resumeListening = false)
                setVoiceModeUi(false)
            }
            addPressFeedback(this)
        }, LinearLayout.LayoutParams(dp(46), dp(46)).apply {
            gravity = Gravity.END
        })

        avatarView = VoiceAvatarView(this).apply {
            setPersonality(Personality.fromKey(store.personality))
            setVoiceState(AvatarState.IDLE)
            contentDescription = "Avatar de voz de DEEP33"
        }
        val maxAvatar = minOf(dp(252), (resources.displayMetrics.widthPixels - dp(52)).coerceAtLeast(dp(190)))
        voicePanel.addView(avatarView, LinearLayout.LayoutParams(maxAvatar, maxAvatar).apply {
            gravity = Gravity.CENTER
            topMargin = dp(28)
            bottomMargin = dp(24)
        })

        // Internal voice-state holder: audio-only mode keeps this view hidden.
        // It must still be initialized because voice callbacks update the state label.
        voiceStateView = TextView(this).apply {
            text = "Modo voz"
            visibility = View.GONE
            contentDescription = null
        }
        voicePanel.addView(voiceStateView, LinearLayout.LayoutParams(1, 1))

        box.addView(voicePanel, LinearLayout.LayoutParams(-1, 0, 1f))

        chatContainer = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(Color.BLACK)
            setPadding(dp(2), dp(10), dp(2), dp(8))
        }
        chatScroll = ScrollView(this).apply {
            isFillViewport = true
            setBackgroundColor(Color.BLACK)
            addView(chatContainer)
        }
        box.addView(chatScroll, LinearLayout.LayoutParams(-1, 0, 1f))

        fun setComposerBackground(target: View, focused: Boolean) {
            target.background = GradientDrawable().apply {
                setColor(Deep33Theme.SURFACE)
                cornerRadius = dp(20).toFloat()
                setStroke(dp(1), if (focused) Personality.fromKey(store.personality).accent else Deep33Theme.LINE_SOFT)
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
            setPadding(dp(16), dp(12), dp(112), dp(12))
            setComposerBackground(this, false)
        }

        val inputShell = FrameLayout(this).apply {
            setPadding(0, dp(4), 0, dp(4))
        }
        inputShell.addView(input, FrameLayout.LayoutParams(-1, ViewGroup.LayoutParams.WRAP_CONTENT))
        input.setOnFocusChangeListener { _, focused -> setComposerBackground(input, focused) }

        micButton = Button(this).apply {
            text = "MIC"
            contentDescription = "Abrir modo voz"
            textSize = 11f
            isAllCaps = false
            minWidth = 0
            minHeight = 0
            gravity = Gravity.CENTER
            setPadding(0, 0, 0, 0)
            setTextColor(Personality.fromKey(store.personality).accent)
            setBackground(chatActionPanel(Deep33Theme.SURFACE_2, Personality.fromKey(store.personality).accent))
            elevation = 0f
            stateListAnimator = null
            setOnClickListener { toggleVoiceInput() }
            addPressFeedback(this)
        }
        inputShell.addView(
            micButton,
            FrameLayout.LayoutParams(dp(44), dp(44), Gravity.END or Gravity.CENTER_VERTICAL).apply {
                marginEnd = dp(56)
            }
        )

        sendButton = Button(this).apply {
            text = "→"
            contentDescription = "Enviar mensaje"
            textSize = 22f
            isAllCaps = false
            minWidth = 0
            minHeight = 0
            gravity = Gravity.CENTER
            setPadding(0, 0, 0, 0)
            val accent = Personality.fromKey(store.personality).accent
            setTextColor(Color.WHITE)
            setBackground(chatActionPanel(Color.rgb(142, 14, 38), accent))
            elevation = 0f
            stateListAnimator = null
            setOnClickListener { sendMessage() }
            addPressFeedback(this)
        }
        inputShell.addView(
            sendButton,
            FrameLayout.LayoutParams(dp(44), dp(44), Gravity.END or Gravity.CENTER_VERTICAL).apply {
                marginEnd = dp(6)
            }
        )

        cancelButton = Button(this).apply {
            text = "■"
            contentDescription = "Detener generación"
            textSize = 17f
            isAllCaps = false
            minWidth = 0
            minHeight = 0
            gravity = Gravity.CENTER
            visibility = View.GONE
            setOnClickListener { cancelGeneration() }
            addPressFeedback(this)
        }
        applyStopButtonTheme(Personality.fromKey(store.personality))

        composer = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.BOTTOM
            setPadding(0, dp(6), 0, dp(4))
            addView(inputShell, LinearLayout.LayoutParams(0, dp(64), 1f).apply {
                setMargins(0, 0, dp(6), 0)
            })
            addView(cancelButton, LinearLayout.LayoutParams(dp(48), dp(48)).apply {
                setMargins(0, dp(4), dp(2), 0)
            })
        }
        box.addView(composer)
        return box
    }

    private fun buildStatus(): View {
        val scroll = ScrollView(this).apply { isFillViewport = true }
        val box = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(8), dp(10), dp(8), dp(24))
        }

        box.addView(TextView(this).apply {
            text = "Estado"
            textSize = 26f
            setTextColor(Deep33Theme.TEXT)
            setTypeface(typeface, android.graphics.Typeface.BOLD)
            setPadding(dp(10), dp(8), dp(10), dp(18))
        })

        val card = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(16), dp(14), dp(16), dp(16))
            setBackground(neonPanel(Deep33Theme.SURFACE, Deep33Theme.LINE))
        }

        card.addView(TextView(this).apply {
            text = "CONECTIVIDAD REAL"
            textSize = 11f
            letterSpacing = 0.10f
            setTextColor(Deep33Theme.TEXT_MUTED)
        })

        diagnosticsView = TextView(this).apply {
            text = "Consultando diagnóstico…"
            textSize = 15f
            setTextColor(Deep33Theme.TEXT)
            setPadding(0, dp(10), 0, dp(16))
        }
        card.addView(diagnosticsView)

        card.addView(Button(this).apply {
            text = "COMPROBAR DE NUEVO"
            isAllCaps = false
            setTextColor(Deep33Theme.TEXT)
            setBackground(neonPanel(Deep33Theme.RED_SURFACE, Deep33Theme.RED_DEEP))
            setOnClickListener { checkConnectivity() }
            addPressFeedback(this)
        }, LinearLayout.LayoutParams(-1, dp(48)))

        box.addView(card)
        scroll.addView(box)
        return scroll
    }

    private fun buildSettings(): View {
        val scroll = ScrollView(this).apply { isFillViewport = true }
        val box = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(8), dp(10), dp(8), dp(24))
        }

        box.addView(TextView(this).apply {
            text = "Configuración"
            textSize = 26f
            setTextColor(Deep33Theme.TEXT)
            setTypeface(typeface, android.graphics.Typeface.BOLD)
            setPadding(dp(10), dp(8), dp(10), dp(18))
        })

        fun section(title: String, subtitle: String, body: View): View {
            val card = LinearLayout(this).apply {
                orientation = LinearLayout.VERTICAL
                setPadding(dp(14), dp(13), dp(14), dp(14))
                setBackground(neonPanel(Deep33Theme.SURFACE, Deep33Theme.LINE))
            }
            card.addView(TextView(this).apply {
                text = title
                textSize = 15f
                setTextColor(Deep33Theme.TEXT)
                setTypeface(typeface, android.graphics.Typeface.BOLD)
            })
            card.addView(TextView(this).apply {
                text = subtitle
                textSize = 12f
                setTextColor(Deep33Theme.TEXT_MUTED)
                setPadding(0, dp(4), 0, dp(10))
            })
            card.addView(body)
            return card
        }

        val voiceBody = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
        }
        voiceBody.addView(Button(this).apply {
            text = if (store.voiceEnabled) "Voz de respuesta · ACTIVADA" else "Voz de respuesta · DESACTIVADA"
            isAllCaps = false
            setTextColor(if (store.voiceEnabled) Color.rgb(105, 226, 158) else Deep33Theme.TEXT_MUTED)
            setBackground(neonPanel(
                if (store.voiceEnabled) Color.rgb(7, 28, 16) else Deep33Theme.SURFACE_2,
                if (store.voiceEnabled) Color.rgb(24, 103, 58) else Deep33Theme.LINE_SOFT
            ))
            setOnClickListener {
                store.voiceEnabled = !store.voiceEnabled
                text = if (store.voiceEnabled) "Voz de respuesta · ACTIVADA" else "Voz de respuesta · DESACTIVADA"
                setTextColor(if (store.voiceEnabled) Color.rgb(105, 226, 158) else Deep33Theme.TEXT_MUTED)
                if (!store.voiceEnabled) {
                    textToSpeech?.stop()
                    setVoiceState(AvatarState.IDLE)
                    setVoiceModeUi(false)
                }
            }
            addPressFeedback(this)
        })

        voiceBody.addView(TextView(this).apply {
            text = "TONO ACTUAL · " + VoiceTone.fromKey(store.voiceTone).key
            textSize = 12f
            setTextColor(Deep33Theme.TEXT_MUTED)
            setPadding(0, dp(12), 0, dp(4))
        })

        VoiceTone.entries.forEach { tone ->
            voiceBody.addView(Button(this).apply {
                text = tone.key + " — " + tone.description
                isAllCaps = false
                setTextColor(if (tone.key == VoiceTone.fromKey(store.voiceTone).key) Deep33Theme.TEXT else Deep33Theme.TEXT_MUTED)
                setBackground(
                    neonPanel(
                        if (tone.key == VoiceTone.fromKey(store.voiceTone).key) Deep33Theme.RED_SURFACE else Deep33Theme.SURFACE_2,
                        if (tone.key == VoiceTone.fromKey(store.voiceTone).key) Deep33Theme.RED else Deep33Theme.LINE_SOFT
                    )
                )
                setOnClickListener {
                    store.voiceTone = tone.key
                    applyVoiceTone()
                    showTab(Tab.SETTINGS)
                }
                addPressFeedback(this)
            })
        }
        box.addView(section(
            "Voz",
            "Audio de respuesta y tono de voz. El modo voz mantiene la interacción sin transcripción visible.",
            voiceBody
        ), LinearLayout.LayoutParams(-1, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
            setMargins(dp(2), 0, dp(2), dp(10))
        })

        val active = Personality.fromKey(store.personality)
        val personalityBody = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
        }
        personalityBody.addView(TextView(this).apply {
            text = active.key + " — " + active.description
            textSize = 14f
            setTextColor(active.accent)
            setPadding(0, 0, 0, dp(10))
        })
        Personality.entries.forEach { option ->
            personalityBody.addView(Button(this).apply {
                val selected = option == active
                text = (if (selected) "●  " else "○  ") + option.key
                isAllCaps = false
                setTextColor(option.accent)
                setBackground(
                    neonPanel(
                        if (selected) Color.rgb(30, 8, 13) else Deep33Theme.SURFACE_2,
                        if (selected) option.accent else Deep33Theme.LINE_SOFT
                    )
                )
                setOnClickListener {
                    selectPersonality(option)
                    showTab(Tab.SETTINGS)
                }
                addPressFeedback(this)
            })
        }
        box.addView(section(
            "Personalidad",
            "La identidad activa se refleja en el chat, avatar y voz.",
            personalityBody
        ), LinearLayout.LayoutParams(-1, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
            setMargins(dp(2), 0, dp(2), dp(10))
        })

        val sessionBody = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
        }
        sessionBody.addView(TextView(this).apply {
            text = "SESIÓN ACTIVA\n" + store.sessionId
            textSize = 12f
            setTextColor(Deep33Theme.TEXT_MUTED)
            setPadding(0, 0, 0, dp(10))
        })
        sessionBody.addView(Button(this).apply {
            text = "BORRAR CONVERSACIÓN"
            isAllCaps = false
            setTextColor(Color.rgb(255, 113, 132))
            setBackground(neonPanel(Color.rgb(24, 7, 11), Color.rgb(112, 25, 41)))
            setOnClickListener {
                conversationRevision++
                conversation.clear()
                store.clearConversation()
                renderConversation()
            }
            addPressFeedback(this)
        })
        sessionBody.addView(Button(this).apply {
            text = "NUEVA SESIÓN"
            isAllCaps = false
            setTextColor(Deep33Theme.TEXT)
            setBackground(neonPanel(Deep33Theme.SURFACE_2, Deep33Theme.LINE_SOFT))
            setOnClickListener { startNewSession() }
            addPressFeedback(this)
        })
        box.addView(section(
            "Sesión",
            "Conversación local persistente y continuidad entre aperturas.",
            sessionBody
        ), LinearLayout.LayoutParams(-1, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
            setMargins(dp(2), 0, dp(2), 0)
        })

        scroll.addView(box)
        return scroll
    }

    private fun voicePanelOrNull(): LinearLayout? =
        if (::voicePanel.isInitialized) voicePanel else null

    private fun selectPersonality(personality: Personality) {
        personalitySelectionGeneration++
        // Persist the selection first so every subsequent chat/voice request uses the
        // exact mode chosen by the user, independent of older memory/preferences.
        store.setPersonalityFromUser(personality.key)
        Log.i("DEEP33", "PERSONALITY ACTIVATED: " + personality.key)
        currentPersonalityView.animate().cancel()
        currentPersonalityView.alpha = 0.25f
        currentPersonalityView.text = "PERSONALIDAD ACTIVA · " + personality.key
        currentPersonalityView.setTextColor(personality.accent)
        currentPersonalityView.setTypeface(currentPersonalityView.typeface, android.graphics.Typeface.BOLD)
        currentPersonalityView.animate().alpha(1f).setDuration(180L).start()
        refreshPersonalityButtons()
        applyChatActionButtonTheme(personality)
        if (::cancelButton.isInitialized) applyStopButtonTheme(personality)
        if (::avatarView.isInitialized) avatarView.setPersonality(personality)
        if (::voicePanel.isInitialized) {
            (voicePanel.background as? GradientDrawable)?.setStroke(dp(1), personality.accent)
            avatarView.animate()
                .scaleX(0.92f).scaleY(0.92f)
                .setDuration(80L)
                .withEndAction {
                    avatarView.setPersonality(personality)
                    avatarView.animate().scaleX(1f).scaleY(1f).setDuration(150L).start()
                }
                .start()
        }
        applyVoiceTone(personality)
        syncPreferences()
    }

    private fun applyStopButtonTheme(personality: Personality) {
        if (!::cancelButton.isInitialized) return
        cancelButton.text = STOP_BUTTON_GLYPH
        cancelButton.setTextColor(Color.WHITE)
        cancelButton.setBackground(
            neonPanel(
                Color.rgb(142, 14, 38),
                personality.accent
            )
        )
    }

    private fun applyPersonalityTheme(personality: Personality) {
        // The header identity follows the active personality: name + neon border.
        currentPersonalityView.text = personality.key
        currentPersonalityView.setTextColor(personality.accent)
        currentPersonalityView.setTypeface(currentPersonalityView.typeface, android.graphics.Typeface.BOLD)
        currentPersonalityView.setBackground(
            GradientDrawable().apply {
                setColor(Color.argb(42, Color.red(personality.accent), Color.green(personality.accent), Color.blue(personality.accent)))
                cornerRadius = dp(16).toFloat()
                setStroke(dp(1), personality.accent)
            }
        )
        currentPersonalityView.paint.setShadowLayer(
            dp(5).toFloat(),
            0f,
            0f,
            personality.accent
        )
        refreshPersonalityButtons()
        applyChatActionButtonTheme(personality)
        if (::cancelButton.isInitialized) applyStopButtonTheme(personality)
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
        conversationRevision++
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
        conversationRevision++
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
        val targetConversationRevision = conversationRevision

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

                runOnUiThread {
                    // The session AND local conversation revision must still match.
                    // Otherwise this remote result belongs to an older UI state and is
                    // forbidden from rebuilding the current chat.
                    if (store.sessionId != targetSessionId || conversationRevision != targetConversationRevision) {
                        return@runOnUiThread
                    }

                    val merged = mutableListOf<UiMessage>()
                    val seen = mutableSetOf<Pair<String, String>>()
                    (remoteMessages + conversation.takeLast(50)).forEach {
                        if (seen.add(it.role to it.content)) merged.add(it)
                    }
                    conversation.clear()
                    conversation.addAll(merged.takeLast(50))
                    conversationRevision++
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
        conversationRevision++
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
            conversationRevision++
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
        if (!generationActive || !activityVisible) return

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
                conversationRevision++
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

    private fun chatActionPanel(fill: Int, stroke: Int): GradientDrawable =
        GradientDrawable().apply {
            setColor(fill)
            cornerRadius = dp(14).toFloat()
            setStroke(dp(1), stroke)
        }

    private fun applyChatActionButtonTheme(personality: Personality) {
        if (::micButton.isInitialized) {
            micButton.setTextColor(personality.accent)
            micButton.setBackground(
                chatActionPanel(Deep33Theme.SURFACE_2, personality.accent)
            )
        }
        if (::sendButton.isInitialized) {
            sendButton.setTextColor(Deep33Theme.BG)
            sendButton.setBackground(
                chatActionPanel(personality.accent, personality.accent)
            )
        }
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
            voicePanel.visibility = View.VISIBLE
            voicePanel.gravity = Gravity.CENTER_HORIZONTAL
            voicePanel.layoutParams = LinearLayout.LayoutParams(-1, 0, 1f)
            chatScroll.visibility = View.GONE
            composer.visibility = View.GONE
            voiceStateView.visibility = View.GONE
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
            setPadding(dp(28), dp(24), dp(28), dp(30))
        }
        panel.addView(TextView(this).apply {
            text = "Intercambia ideas. Explora. Cuestiona."
            textSize = 14f
            setTextColor(Deep33Theme.TEXT_MUTED)
            gravity = Gravity.CENTER
        }, LinearLayout.LayoutParams(-1, ViewGroup.LayoutParams.WRAP_CONTENT))
        panel.addView(TextView(this).apply {
            text = "Memoria, personalidad y voz integradas en una sola conversación."
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
                if (isAssistant) 0.91f else 0.78f).roundToInt().coerceAtLeast(dp(120))
        }
        bubble.addView(contentView)
        renderMarkdown(contentView, content, isAssistant)

        if (isAssistant) {
            val speakerButton = Button(this).apply {
                text = "🔊"
                contentDescription = "Reproducir respuesta de DEEP33"
                textSize = 17f
                isAllCaps = false
                minWidth = 0
                minHeight = 0
                gravity = Gravity.CENTER
                setPadding(0, 0, 0, 0)
                setTextColor(Color.WHITE)
                setBackground(chatActionPanel(Color.rgb(110, 12, 28), accent))
                elevation = 0f
                stateListAnimator = null
                setOnClickListener { speakAssistant(content, Personality.fromKey(store.personality)) }
                addPressFeedback(this)
            }
            bubble.addView(
                speakerButton,
                LinearLayout.LayoutParams(dp(44), dp(40)).apply {
                    topMargin = dp(8)
                }
            )
        }

        val params = LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.WRAP_CONTENT,
            ViewGroup.LayoutParams.WRAP_CONTENT
        ).apply {
            gravity = if (isAssistant) Gravity.START else Gravity.END
            setMargins(
                if (isAssistant) dp(2) else dp(46),
                0,
                if (isAssistant) dp(8) else dp(2),
                dp(10)
            )
        }
        chatContainer.addView(bubble, params)
        // No entry animation: render immediately to reduce visual overhead and latency.
        bubble.alpha = 1f
        bubble.translationY = 0f
        addPressFeedback(bubble)
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
        val scroll = chatContainer.parent as? ScrollView ?: return
        scroll.post { scroll.scrollTo(0, chatContainer.height) }
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
