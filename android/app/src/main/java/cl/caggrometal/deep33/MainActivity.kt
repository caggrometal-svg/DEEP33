package cl.caggrometal.deep33

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.os.Bundle
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
import java.util.concurrent.Future
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.math.roundToInt

private enum class ConnectionState { CONNECTING, ONLINE, OFFLINE }

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

    private val executor = Executors.newFixedThreadPool(2)
    private lateinit var store: SessionStore
    private val conversation = mutableListOf<UiMessage>()
    private var activeTask: Future<*>? = null
    private var activeBubble: TextView? = null
    private val cancelRequested = AtomicBoolean(false)
    private var textToSpeech: TextToSpeech? = null
    private var speechRecognizer: SpeechRecognizer? = null
    private var speechListening = false
    private var edgeSwipeTracking = false
    private var edgeDownX = 0f
    private var edgeDownY = 0f

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        store = SessionStore(this)
        conversation.addAll(store.loadMessages())

        textToSpeech = TextToSpeech(this) { status ->
            if (status == TextToSpeech.SUCCESS) {
                val locale = Locale("es", "CL")
                val result = textToSpeech?.setLanguage(locale)
                if (result == TextToSpeech.LANG_MISSING_DATA || result == TextToSpeech.LANG_NOT_SUPPORTED) {
                    textToSpeech?.setLanguage(Locale("es"))
                }
            }
        }
        textToSpeech?.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
            override fun onStart(utteranceId: String?) {
                runOnUiThread { setVoiceState(AvatarState.SPEAKING) }
            }

            override fun onDone(utteranceId: String?) {
                runOnUiThread {
                    setVoiceState(AvatarState.IDLE)
                    voicePanelOrNull()?.visibility = View.GONE
                }
            }

            override fun onError(utteranceId: String?) {
                runOnUiThread {
                    setVoiceState(AvatarState.IDLE)
                    voicePanelOrNull()?.visibility = View.GONE
                }
            }
        })

        if (SpeechRecognizer.isRecognitionAvailable(this)) {
            speechRecognizer = SpeechRecognizer.createSpeechRecognizer(this).apply {
                setRecognitionListener(recognitionListener)
            }
        }

        window.statusBarColor = Color.rgb(8, 10, 15)
        window.navigationBarColor = Color.rgb(8, 10, 15)
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
    }

    override fun onDestroy() {
        cancelRequested.set(true)
        stopVoiceInput()
        Deep33Api.cancelActiveStream()
        activeTask?.cancel(true)
        executor.shutdownNow()
        speechRecognizer?.destroy()
        speechRecognizer = null
        textToSpeech?.stop()
        textToSpeech?.shutdown()
        textToSpeech = null
        super.onDestroy()
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
            setBackgroundColor(Color.rgb(8, 10, 15))
        }

        val main = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(Color.rgb(8, 10, 15))
            setPadding(dp(12), dp(8), dp(12), dp(8))
        }

        val header = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(dp(4), dp(2), dp(4), dp(12))
        }

        header.addView(Button(this).apply {
            text = "☰"
            textSize = 20f
            minWidth = dp(50)
            minHeight = dp(48)
            setOnClickListener { toggleSidebar() }
        }, LinearLayout.LayoutParams(dp(56), dp(54)))

        statusView = TextView(this).apply {
            text = "PROCESANDO · DEEP33"
            textSize = 12f
            setPadding(0, dp(2), 0, 0)
        }

        val titleGroup = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER_VERTICAL
        }
        titleGroup.addView(TextView(this).apply {
            text = "DEEP33"
            setTextColor(Color.WHITE)
            textSize = 24f
            setTypeface(typeface, android.graphics.Typeface.BOLD)
        })
        currentPersonalityView = TextView(this).apply {
            textSize = 12f
            setPadding(0, dp(2), 0, 0)
        }
        titleGroup.addView(currentPersonalityView)

        header.addView(
            titleGroup,
            LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply {
                setMargins(dp(8), 0, 0, 0)
            }
        )
        main.addView(header, ViewGroup.LayoutParams(-1, ViewGroup.LayoutParams.WRAP_CONTENT))

        contentFrame = FrameLayout(this)
        main.addView(contentFrame, LinearLayout.LayoutParams(-1, 0, 1f))
        main.addView(buildNavigation(), ViewGroup.LayoutParams(-1, ViewGroup.LayoutParams.WRAP_CONTENT))
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

    private fun buildNavigation(): View {
        val nav = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER
            setPadding(0, dp(6), 0, dp(2))
        }
        nav.addView(navButton("CHAT") { showTab(Tab.CHAT) }, weightParams())
        nav.addView(navButton("ESTADO") { showTab(Tab.STATUS) }, weightParams())
        return nav
    }

    private fun weightParams() =
        LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply {
            setMargins(dp(3), 0, dp(3), 0)
        }

    private fun navButton(label: String, action: () -> Unit): Button =
        Button(this).apply {
            text = label
            textSize = 10f
            minHeight = dp(50)
            isAllCaps = false
            setOnClickListener { action() }
        }

    private fun buildSidebar(): LinearLayout {
        val panel = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(18), dp(28), dp(14), dp(16))
            setBackgroundColor(Color.rgb(8, 10, 15))
            elevation = dp(12).toFloat()
        }

        val header = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        header.addView(
            TextView(this).apply {
                text = "Chats"
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
            text = "＋  Nuevo chat"
            isAllCaps = false
            setTextColor(Color.rgb(0, 255, 140))
            setBackground(neonPanel(Color.rgb(18, 28, 27), Color.rgb(0, 255, 140)))
            setOnClickListener {
                startNewSession()
                hideSidebar()
            }
        }, ViewGroup.LayoutParams(-1, ViewGroup.LayoutParams.WRAP_CONTENT))

        panel.addView(Button(this).apply {
            text = "⚙  Configuración"
            isAllCaps = false
            setTextColor(Color.rgb(255, 70, 90))
            setBackground(neonPanel(Color.rgb(30, 18, 22), Color.rgb(255, 70, 90)))
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

        val modes = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        Personality.entries.forEach { option ->
            modes.addView(personalityButton(option))
        }
        panel.addView(modes)

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

    private fun personalityButton(option: Personality): Button =
        Button(this).apply {
            text = option.key + " · " + option.description
            isAllCaps = false
            gravity = Gravity.START or Gravity.CENTER_VERTICAL
            minHeight = dp(54)
            setTextColor(option.accent)
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
                    setColor(Color.rgb(15, 22, 31))
                    cornerRadius = dp(22).toFloat()
                    setStroke(dp(1), Personality.fromKey(store.personality).accent)
                }
            )
            setOnClickListener { toggleVoiceInput() }
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
            setPadding(dp(4), dp(12), dp(4), dp(12))
        }
        val scroll = ScrollView(this).apply {
            isFillViewport = true
            addView(chatContainer)
        }
        box.addView(scroll, LinearLayout.LayoutParams(-1, 0, 1f))

        input = EditText(this).apply {
            hint = "Escribe un mensaje"
            setHintTextColor(Color.rgb(130, 138, 150))
            setTextColor(Color.WHITE)
            textSize = 16f
            maxLines = 5
            gravity = Gravity.TOP
            setBackground(
                GradientDrawable().apply {
                    setColor(Color.rgb(18, 22, 30))
                    cornerRadius = dp(22).toFloat()
                    setStroke(dp(1), Color.rgb(46, 54, 68))
                }
            )
            setPadding(dp(16), dp(12), dp(16), dp(12))
        }

        micButton = Button(this).apply {
            text = "MIC"
            minWidth = dp(58)
            minHeight = dp(58)
            setOnClickListener { toggleVoiceInput() }
        }
        sendButton = Button(this).apply {
            text = "ENVIAR"
            minHeight = dp(58)
            setOnClickListener { sendMessage() }
        }
        cancelButton = Button(this).apply {
            text = "CANCELAR"
            minHeight = dp(58)
            visibility = View.GONE
            setOnClickListener { cancelGeneration() }
        }

        val composer = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.BOTTOM
            setPadding(0, dp(8), 0, 0)
            addView(micButton, LinearLayout.LayoutParams(dp(62), dp(58)).apply {
                setMargins(0, 0, dp(6), 0)
            })
            addView(input, LinearLayout.LayoutParams(0, dp(58), 1f).apply {
                setMargins(0, 0, dp(6), 0)
            })
            addView(sendButton, LinearLayout.LayoutParams(dp(86), dp(58)).apply {
                setMargins(0, 0, dp(6), 0)
            })
            addView(cancelButton, LinearLayout.LayoutParams(dp(88), dp(58)))
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

        box.addView(TextView(this).apply {
            text = "Sesión\\n" + store.sessionId
            setTextColor(Color.LTGRAY)
            textSize = 13f
            setPadding(dp(16), dp(12), dp(16), dp(12))
        })

        box.addView(Button(this).apply {
            text = if (store.voiceEnabled) "VOZ DE RESPUESTA: ACTIVADA" else "VOZ DE RESPUESTA: DESACTIVADA"
            isAllCaps = false
            setOnClickListener {
                store.voiceEnabled = !store.voiceEnabled
                text = if (store.voiceEnabled) "VOZ DE RESPUESTA: ACTIVADA" else "VOZ DE RESPUESTA: DESACTIVADA"
                if (!store.voiceEnabled) {
                    textToSpeech?.stop()
                    setVoiceState(AvatarState.IDLE)
                    voicePanelOrNull()?.visibility = View.GONE
                }
            }
        })

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
                setOnClickListener {
                    selectPersonality(option)
                    showTab(Tab.SETTINGS)
                }
            })
        }

        box.addView(Button(this).apply {
            text = "BORRAR CONVERSACIÓN"
            setOnClickListener {
                conversation.clear()
                store.clearConversation()
                renderConversation()
            }
        })
        box.addView(Button(this).apply {
            text = "NUEVA SESIÓN"
            setOnClickListener { startNewSession() }
        })
        return box
    }

    private fun voicePanelOrNull(): LinearLayout? =
        if (::voicePanel.isInitialized) voicePanel else null

    private fun selectPersonality(personality: Personality) {
        store.personality = personality.key
        currentPersonalityView.text = "Modo: " + personality.key
        currentPersonalityView.setTextColor(personality.accent)
        if (::sendButton.isInitialized) sendButton.setTextColor(personality.accent)
        if (::cancelButton.isInitialized) cancelButton.setTextColor(personality.accent)
        if (::micButton.isInitialized) micButton.setTextColor(personality.accent)
        if (::avatarView.isInitialized) avatarView.setPersonality(personality)
        if (::voicePanel.isInitialized) {
            (voicePanel.background as? GradientDrawable)?.setStroke(dp(1), personality.accent)
        }
        syncPreferences()
    }

    private fun applyPersonalityTheme(personality: Personality) {
        currentPersonalityView.text = "Modo: " + personality.key
        currentPersonalityView.setTextColor(personality.accent)
        if (::sendButton.isInitialized) sendButton.setTextColor(personality.accent)
        if (::cancelButton.isInitialized) cancelButton.setTextColor(personality.accent)
        if (::micButton.isInitialized) micButton.setTextColor(personality.accent)
        if (::avatarView.isInitialized) avatarView.setPersonality(personality)
        if (::voicePanel.isInitialized) {
            (voicePanel.background as? GradientDrawable)?.setStroke(dp(1), personality.accent)
        }
    }

    private fun syncPreferences() {
        val personality = store.personality
        executor.submit {
            try {
                Deep33Api.setPreferences(store.sessionId, personality)
            } catch (e: Exception) {
                Log.w("DEEP33", "Remote preference sync failed: ${e.javaClass.simpleName}")
            }
        }
    }

    private fun startNewSession() {
        if (activeTask?.isDone == false) cancelGeneration()
        saveCurrentSummary()
        stopVoiceInput()
        textToSpeech?.stop()
        store.resetSession()
        conversation.clear()
        showTab(Tab.CHAT)
        renderConversation()
        refreshSidebarHistory()
    }

    private fun openSession(sessionId: String) {
        if (activeTask?.isDone == false) cancelGeneration()
        stopVoiceInput()
        textToSpeech?.stop()
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

    private fun loadRemoteContext() {
        executor.submit {
            try {
                val sessionId = store.sessionId
                val remote = Deep33Api.memoryContext(sessionId)
                val session = remote.optJSONObject("session")
                val remotePersonality = session?.optString("personality").orEmpty()
                if (remotePersonality.isNotBlank()) store.personality = remotePersonality

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
                (remoteMessages + conversation.takeLast(50)).forEach {
                    if (seen.add(it.role to it.content)) merged.add(it)
                }

                conversation.clear()
                conversation.addAll(merged.takeLast(50))
                store.saveMessages(conversation)

                runOnUiThread {
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
                val diagnostics = Deep33Api.get("/v1/ai/diagnostics", store.sessionId)
                val online = health.optString("status") == "PASS" &&
                    diagnostics.optBoolean("online", false)
                val gateway = diagnostics.optJSONObject("gateway")
                val provider = gateway?.optString("provider", "") ?: ""
                val model = gateway?.optString("model", "") ?: ""
                val summary = if (online) {
                    "ONLINE\\nBACKEND: PASS\\nAI GATEWAY: PASS\\nPROVIDER: " +
                        provider + "\\nMODEL: " + model
                } else {
                    "OFFLINE\\nRevisa conectividad y diagnóstico del backend."
                }
                runOnUiThread {
                    updateConnection(if (online) ConnectionState.ONLINE else ConnectionState.OFFLINE)
                    if (::diagnosticsView.isInitialized) diagnosticsView.text = summary
                }
            } catch (e: Deep33ApiException) {
                runOnUiThread {
                    updateConnection(ConnectionState.OFFLINE)
                    if (::diagnosticsView.isInitialized) {
                        diagnosticsView.text = "OFFLINE\\n" + (e.message ?: "Error de conectividad.")
                    }
                }
            } catch (_: Exception) {
                runOnUiThread {
                    updateConnection(ConnectionState.OFFLINE)
                    if (::diagnosticsView.isInitialized) {
                        diagnosticsView.text = "OFFLINE\\nError inesperado de conectividad."
                    }
                }
            }
        }
    }

    private fun sendMessage(textOverride: String? = null) {
        val text = (textOverride ?: input.text.toString()).trim()
        if (text.isEmpty() || activeTask?.isDone == false) return

        stopVoiceInput()
        voicePanelOrNull()?.visibility = View.VISIBLE
        setVoiceState(AvatarState.THINKING)

        conversation.add(UiMessage("user", text))
        store.saveMessages(conversation)
        saveCurrentSummary()
        appendBubble("TÚ", text, Color.rgb(12, 34, 27))
        input.setText("")
        refreshSidebarHistory()

        activeBubble = appendBubble("DEEP33", "Pensando...", Color.rgb(42, 12, 18))
        cancelRequested.set(false)
        sendButton.isEnabled = false
        input.isEnabled = false
        micButton.isEnabled = false
        cancelButton.visibility = View.VISIBLE
        updateConnection(ConnectionState.CONNECTING)

        val payload = org.json.JSONArray()
        conversation.takeLast(50).forEach {
            payload.put(org.json.JSONObject().put("role", it.role).put("content", it.content))
        }

        activeTask = executor.submit {
            try {
                val requestId = UUID.randomUUID().toString()
                val idempotencyKey = "chat-" + requestId
                val finalText = try {
                    Deep33Api.stream(
                        payload,
                        store.sessionId,
                        store.personality,
                        requestId = requestId,
                        idempotencyKey = idempotencyKey,
                        isCancelled = { cancelRequested.get() || Thread.currentThread().isInterrupted },
                        onText = { chunk ->
                            runOnUiThread {
                                val bubble = activeBubble ?: return@runOnUiThread
                                val current = bubble.tag as? String ?: ""
                                val next = current + chunk
                                bubble.tag = next
                                renderMarkdown(bubble, next)
                                setVoiceState(AvatarState.SPEAKING)
                            }
                        }
                    )
                } catch (streamError: Deep33ApiException) {
                    throw streamError
                }

                if (finalText.isBlank()) throw Deep33ApiException(Deep33ApiException.Kind.BAD_RESPONSE)

                conversation.add(UiMessage("assistant", finalText))
                store.saveMessages(conversation)
                saveCurrentSummary()

                runOnUiThread {
                    speakAssistant(finalText)
                    cleanupGeneration(true)
                    refreshSidebarHistory()
                }
            } catch (e: Deep33ApiException) {
                runOnUiThread {
                    val bubble = activeBubble
                    if (e.kind == Deep33ApiException.Kind.CANCELLED || cancelRequested.get()) {
                        bubble?.let {
                            it.tag = "Generación cancelada."
                            renderMarkdown(it, "Generación cancelada.")
                        }
                    } else {
                        bubble?.let {
                            it.tag = e.message ?: "Error de comunicación."
                            it.text = e.message ?: "Error de comunicación."
                        }
                    }
                    cleanupGeneration(false)
                }
            } catch (_: Exception) {
                runOnUiThread {
                    activeBubble?.let {
                        it.tag = "Error inesperado de comunicación."
                        it.text = "Error inesperado de comunicación."
                    }
                    cleanupGeneration(false)
                }
            }
        }
    }

    private fun cancelGeneration() {
        if (activeTask?.isDone != false) return
        cancelRequested.set(true)
        Deep33Api.cancelActiveStream()
        activeTask?.cancel(true)
        activeBubble?.let {
            it.tag = "Generación cancelada."
            renderMarkdown(it, "Generación cancelada.")
        }
        cleanupGeneration(false)
    }

    private fun cleanupGeneration(success: Boolean) {
        activeTask = null
        activeBubble = null
        sendButton.isEnabled = true
        input.isEnabled = true
        micButton.isEnabled = true
        cancelButton.visibility = View.GONE
        updateConnection(if (success) ConnectionState.ONLINE else ConnectionState.OFFLINE)
        if (!success || !store.voiceEnabled || textToSpeech?.isSpeaking != true) {
            setVoiceState(AvatarState.IDLE)
            voicePanelOrNull()?.visibility = View.GONE
        }
    }

    private fun neonPanel(fill: Int, stroke: Int): GradientDrawable =
        GradientDrawable().apply {
            setColor(fill)
            cornerRadius = dp(16).toFloat()
            setStroke(dp(1), stroke)
        }

    private fun updateConnection(state: ConnectionState) {
        val (text, color) = when (state) {
            ConnectionState.CONNECTING -> "PROCESANDO · DEEP33" to Color.rgb(255, 193, 7)
            ConnectionState.ONLINE -> "ONLINE · DEEP33" to Color.rgb(0, 255, 140)
            ConnectionState.OFFLINE -> "OFFLINE · DEEP33" to Color.rgb(255, 80, 80)
        }
        if (::statusView.isInitialized) {
            statusView.text = text
            statusView.setTextColor(color)
        }
    }

    private fun speakAssistant(text: String) {
        if (!store.voiceEnabled || text.isBlank()) return
        voicePanelOrNull()?.visibility = View.VISIBLE
        setVoiceState(AvatarState.SPEAKING)
        val speech = text
            .replace(Regex("\\[([^]]+)]\\(([^)]+)\\)"), "$1")
            .replace(Regex("[*_#>]"), "")
            .replace("\u0060", "")
        textToSpeech?.speak(
            speech,
            TextToSpeech.QUEUE_FLUSH,
            null,
            "deep33-response-" + System.currentTimeMillis()
        )
    }

    private fun renderConversation() {
        if (!::chatContainer.isInitialized) return
        chatContainer.removeAllViews()
        conversation.takeLast(50).forEach { message ->
            appendBubble(
                if (message.role == "user") "TÚ" else "DEEP33",
                message.content,
                if (message.role == "user") Color.rgb(12, 34, 27) else Color.rgb(42, 12, 18)
            )
        }
        chatContainer.post { scrollToBottom() }
    }

    private fun appendBubble(label: String, content: String, background: Int): TextView {
        val isAssistant = label == "DEEP33"
        val bubble = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(16), dp(12), dp(16), dp(12))
            setBackground(
                GradientDrawable().apply {
                    setColor(background)
                    cornerRadius = dp(22).toFloat()
                    val neon = if (isAssistant) Color.rgb(255, 45, 70) else Color.rgb(0, 255, 140)
                    setStroke(dp(2), neon)
                }
            )
        }

        bubble.addView(TextView(this).apply {
            text = label
            setTextColor(if (isAssistant) Personality.fromKey(store.personality).accent else Color.LTGRAY)
            textSize = 11f
            setTypeface(typeface, android.graphics.Typeface.BOLD)
        })

        val contentView = TextView(this).apply {
            tag = content
            textSize = 16f
            setTextColor(Color.WHITE)
            setPadding(0, dp(6), 0, 0)
            movementMethod = LinkMovementMethod.getInstance()
        }
        bubble.addView(contentView)
        renderMarkdown(contentView, content)

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

    private fun renderMarkdown(view: TextView, markdown: String) {
        view.text = MarkdownRenderer.render(markdown)
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
        if (speechListening) stopVoiceInput() else startVoiceInput()
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

        voicePanelOrNull()?.visibility = View.VISIBLE
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
        if (!speechListening) return
        speechListening = false
        speechRecognizer?.stopListening()
        setVoiceState(AvatarState.IDLE)
        if (activeTask?.isDone != false) voicePanelOrNull()?.visibility = View.GONE
    }

    private val recognitionListener = object : RecognitionListener {
        override fun onReadyForSpeech(params: Bundle?) {
            speechListening = true
            setVoiceState(AvatarState.LISTENING)
        }

        override fun onBeginningOfSpeech() {
            setVoiceState(AvatarState.LISTENING)
        }

        override fun onRmsChanged(rmsdB: Float) {
            if (::avatarView.isInitialized) {
                avatarView.setAudioLevel((rmsdB / 10f).coerceIn(0f, 1f))
            }
        }

        override fun onBufferReceived(buffer: ByteArray?) = Unit

        override fun onEndOfSpeech() {
            speechListening = false
            setVoiceState(AvatarState.THINKING)
        }

        override fun onError(error: Int) {
            speechListening = false
            setVoiceState(AvatarState.IDLE)
            if (activeTask?.isDone != false) voicePanelOrNull()?.visibility = View.GONE
        }

        override fun onResults(results: Bundle?) {
            speechListening = false
            val recognized = results
                ?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                ?.firstOrNull()
                .orEmpty()
                .trim()
            if (recognized.isNotBlank()) {
                input.setText(recognized)
                input.setSelection(input.text.length)
                sendMessage(recognized)
            } else {
                setVoiceState(AvatarState.IDLE)
                voicePanelOrNull()?.visibility = View.GONE
            }
        }

        override fun onPartialResults(partialResults: Bundle?) {
            val partial = partialResults
                ?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                ?.firstOrNull()
                .orEmpty()
                .trim()
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
