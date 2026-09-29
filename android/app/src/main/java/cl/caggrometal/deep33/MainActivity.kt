package cl.caggrometal.deep33

import android.Manifest
import android.app.Activity
import android.content.ClipData
import android.content.ClipboardManager
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
import android.widget.SeekBar
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
    private lateinit var voiceModeOverlay: LinearLayout
    private lateinit var voiceStateView: TextView
    private lateinit var avatarView: VoiceAvatarView
    private var voiceModeActive = false
    private var voiceVolume = 0.85f
    private var voiceTone = VoiceTone.NEUTRO
    private val voiceToneButtons = mutableMapOf<VoiceTone, Button>()

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
    private var drawerSwipeTracking = false
    private var drawerDownX = 0f
    private var drawerDownY = 0f

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
                applyVoiceTone()
            }
        }
        textToSpeech?.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
            override fun onStart(utteranceId: String?) {
                runOnUiThread { setVoiceState(AvatarState.SPEAKING) }
            }

            override fun onDone(utteranceId: String?) {
                runOnUiThread {
                    setVoiceState(AvatarState.IDLE)
                    if (!voiceModeActive) voicePanelOrNull()?.visibility = View.GONE
                }
            }

            override fun onError(utteranceId: String?) {
                runOnUiThread {
                    setVoiceState(AvatarState.IDLE)
                    if (!voiceModeActive) voicePanelOrNull()?.visibility = View.GONE
                }
            }
        })

        if (SpeechRecognizer.isRecognitionAvailable(this)) {
            speechRecognizer = SpeechRecognizer.createSpeechRecognizer(this).apply {
                setRecognitionListener(recognitionListener)
            }
        }

        window.statusBarColor = Color.rgb(5, 6, 7)
        window.navigationBarColor = Color.rgb(5, 6, 7)
        window.setSoftInputMode(android.view.WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE)
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

    private enum class VoiceTone(val label: String, val rate: Float, val pitch: Float) {
        CLARO("Claro", 1.04f, 1.08f),
        NEUTRO("Neutro", 1.00f, 1.00f),
        PROFUNDO("Profundo", 0.90f, 0.82f)
    }

    private fun addPressFeedback(view: View) {
        view.setOnTouchListener { touched, event ->
            when (event.actionMasked) {
                android.view.MotionEvent.ACTION_DOWN -> touched.animate().scaleX(0.97f).scaleY(0.97f).setDuration(70L).start()
                android.view.MotionEvent.ACTION_UP, android.view.MotionEvent.ACTION_CANCEL -> touched.animate().scaleX(1f).scaleY(1f).setDuration(110L).start()
            }
            false
        }
    }
    private fun dp(value: Int): Int =
        (value * resources.displayMetrics.density).roundToInt()

    override fun dispatchTouchEvent(event: android.view.MotionEvent): Boolean {
        when (event.actionMasked) {
            android.view.MotionEvent.ACTION_DOWN -> {
                if (::sidebar.isInitialized && sidebar.visibility == View.VISIBLE) {
                    drawerSwipeTracking = true
                    drawerDownX = event.x
                    drawerDownY = event.y
                } else {
                    edgeSwipeTracking = event.x <= dp(28)
                    edgeDownX = event.x
                    edgeDownY = event.y
                }
            }
            android.view.MotionEvent.ACTION_UP, android.view.MotionEvent.ACTION_CANCEL -> {
                if (event.actionMasked == android.view.MotionEvent.ACTION_UP) {
                    val dx = event.x - drawerDownX
                    val dy = kotlin.math.abs(event.y - drawerDownY)
                    if (drawerSwipeTracking && dx <= -dp(72) && dy <= dp(96)) {
                        hideSidebar()
                    } else if (edgeSwipeTracking) {
                        val edgeDx = event.x - edgeDownX
                        val edgeDy = kotlin.math.abs(event.y - edgeDownY)
                        if (edgeDx >= dp(72) && edgeDy <= dp(96)) showSidebar()
                    }
                }
                drawerSwipeTracking = false
                edgeSwipeTracking = false
            }
        }
        return super.dispatchTouchEvent(event)
    }
    private fun buildRoot(): View {
        rootFrame = FrameLayout(this).apply {
            setBackgroundColor(Color.rgb(5, 6, 7))
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

        voiceModeOverlay = buildVoiceModeOverlay()
        rootFrame.addView(voiceModeOverlay, FrameLayout.LayoutParams(-1, -1))
        voiceModeOverlay.visibility = View.GONE

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
            addPressFeedback(this)
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
        drawerScrim.animate().cancel()
        sidebar.animate().cancel()
        drawerScrim.alpha = 0f
        drawerScrim.visibility = View.VISIBLE
        sidebar.visibility = View.VISIBLE
        sidebar.translationX = -dp(322).toFloat()
        drawerScrim.animate().alpha(1f).setDuration(220L).start()
        sidebar.animate()
            .translationX(0f)
            .setDuration(260L)
            .setInterpolator(android.view.animation.DecelerateInterpolator())
            .start()
    }

    private fun hideSidebar() {
        if (sidebar.visibility != View.VISIBLE) return
        drawerScrim.animate().cancel()
        sidebar.animate().cancel()
        drawerScrim.animate().alpha(0f).setDuration(180L).start()
        sidebar.animate()
            .translationX(-dp(322).toFloat())
            .setDuration(210L)
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
        contentFrame.animate().cancel()
        contentFrame.alpha = 0f
        contentFrame.translationY = dp(8).toFloat()
        contentFrame.removeAllViews()
        when (tab) {
            Tab.CHAT -> contentFrame.addView(buildChat())
            Tab.STATUS -> contentFrame.addView(buildStatus())
            Tab.SETTINGS -> contentFrame.addView(buildSettings())
        }
        contentFrame.animate().alpha(1f).translationY(0f).setDuration(180L).setInterpolator(android.view.animation.DecelerateInterpolator()).start()
    }
    private fun buildChat(): View {
        val box = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }

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
            setHintTextColor(Color.rgb(122, 130, 142))
            setTextColor(Color.WHITE)
            textSize = 16f
            minLines = 1
            maxLines = 5
            gravity = Gravity.CENTER_VERTICAL
            isSingleLine = false
            setHorizontallyScrolling(false)
            setPadding(dp(54), dp(13), dp(58), dp(13))
            setBackground(GradientDrawable().apply {
                setColor(Color.rgb(17, 21, 29))
                cornerRadius = dp(28).toFloat()
                setStroke(dp(1), Color.rgb(45, 52, 65))
            })
        }

        fun composerActionButton(symbol: String, tint: Int, description: String, action: () -> Unit): Button =
            Button(this).apply {
                text = symbol
                textSize = 19f
                minWidth = dp(48)
                minHeight = dp(48)
                contentDescription = description
                gravity = Gravity.CENTER
                setTextColor(tint)
                setBackground(GradientDrawable().apply {
                    setColor(Color.TRANSPARENT)
                    cornerRadius = dp(24).toFloat()
                })
                setOnClickListener { action() }
            }

        val composerShell = FrameLayout(this).apply {
            setPadding(0, dp(6), 0, dp(6))
        }
        composerShell.addView(input, FrameLayout.LayoutParams(-1, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
            gravity = Gravity.CENTER_VERTICAL
        })

        micButton = composerActionButton("MIC", Color.rgb(0, 255, 140), "Abrir modo voz") {
            toggleVoiceInput()
        }
        composerShell.addView(micButton, FrameLayout.LayoutParams(dp(48), dp(48)).apply {
            gravity = Gravity.START or Gravity.CENTER_VERTICAL
            leftMargin = dp(4)
        })

        sendButton = composerActionButton("➤", Color.rgb(255, 42, 68), "Enviar mensaje") {
            sendMessage()
        }
        composerShell.addView(sendButton, FrameLayout.LayoutParams(dp(48), dp(48)).apply {
            gravity = Gravity.END or Gravity.CENTER_VERTICAL
            rightMargin = dp(4)
        })

        cancelButton = composerActionButton("■", Color.rgb(255, 42, 68), "Cancelar generación") {
            cancelGeneration()
        }
        cancelButton.visibility = View.GONE
        composerShell.addView(cancelButton, FrameLayout.LayoutParams(dp(48), dp(48)).apply {
            gravity = Gravity.END or Gravity.CENTER_VERTICAL
            rightMargin = dp(4)
        })

        box.addView(composerShell, LinearLayout.LayoutParams(-1, ViewGroup.LayoutParams.WRAP_CONTENT))
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
        val scroll = ScrollView(this).apply {
            isFillViewport = true
        }
        val box = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(8), dp(10), dp(8), dp(24))
        }

        box.addView(TextView(this).apply {
            text = "Configuración"
            textSize = 26f
            setTextColor(Color.WHITE)
            setTypeface(typeface, android.graphics.Typeface.BOLD)
            setPadding(dp(10), dp(8), dp(10), dp(18))
        })

        fun card(title: String, subtitle: String, body: View): LinearLayout {
            val card = LinearLayout(this).apply {
                orientation = LinearLayout.VERTICAL
                setPadding(dp(14), dp(12), dp(14), dp(12))
                setBackground(GradientDrawable().apply {
                    setColor(Color.rgb(13, 16, 20))
                    cornerRadius = dp(18).toFloat()
                    setStroke(dp(1), Color.rgb(39, 45, 53))
                })
            }
            card.addView(TextView(this).apply {
                text = title
                textSize = 15f
                setTextColor(Color.WHITE)
                setTypeface(typeface, android.graphics.Typeface.BOLD)
            }, LinearLayout.LayoutParams(-1, ViewGroup.LayoutParams.WRAP_CONTENT))
            card.addView(TextView(this).apply {
                text = subtitle
                textSize = 12f
                setTextColor(Color.rgb(126, 135, 147))
                setPadding(0, dp(3), 0, dp(9))
            }, LinearLayout.LayoutParams(-1, ViewGroup.LayoutParams.WRAP_CONTENT))
            card.addView(body, ViewGroup.LayoutParams(-1, ViewGroup.LayoutParams.WRAP_CONTENT))
            return card
        }

        val appearance = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            addView(TextView(this@MainActivity).apply {
                text = "Negro profundo · rojo principal · verde neón como acento"
                textSize = 13f
                setTextColor(Color.rgb(190, 198, 208))
            })
        }
        box.addView(card("Apariencia", "Identidad visual de DEEP33", appearance), LinearLayout.LayoutParams(-1, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
            setMargins(0, 0, 0, dp(10))
        })

        val voiceBody = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
        }
        voiceBody.addView(Button(this@MainActivity).apply {
            text = if (store.voiceEnabled) "Respuestas de voz: ACTIVADAS" else "Respuestas de voz: DESACTIVADAS"
            isAllCaps = false
            setOnClickListener {
                store.voiceEnabled = !store.voiceEnabled
                text = if (store.voiceEnabled) "Respuestas de voz: ACTIVADAS" else "Respuestas de voz: DESACTIVADAS"
                if (!store.voiceEnabled) textToSpeech?.stop()
            }
        })
        voiceBody.addView(Button(this@MainActivity).apply {
            text = "Abrir modo voz"
            isAllCaps = false
            setTextColor(Color.rgb(0, 255, 140))
            setOnClickListener { startVoiceInput() }
        })
        box.addView(card("Voz", "Entrada, salida, tono y volumen se controlan en modo voz", voiceBody), LinearLayout.LayoutParams(-1, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
            setMargins(0, 0, 0, dp(10))
        })

        val personalityBody = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
        }
        Personality.entries.forEach { option ->
            personalityBody.addView(Button(this@MainActivity).apply {
                text = option.key + " · " + option.description
                isAllCaps = false
                gravity = Gravity.START or Gravity.CENTER_VERTICAL
                minHeight = dp(48)
                setTextColor(option.accent)
                setOnClickListener {
                    selectPersonality(option)
                    showTab(Tab.SETTINGS)
                }
            })
        }
        box.addView(card("Personalidad", "Modo actual: " + Personality.fromKey(store.personality).key, personalityBody), LinearLayout.LayoutParams(-1, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
            setMargins(0, 0, 0, dp(10))
        })

        val conversationBody = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
        }
        conversationBody.addView(Button(this@MainActivity).apply {
            text = "Borrar conversación actual"
            isAllCaps = false
            setOnClickListener {
                conversation.clear()
                store.clearConversation()
                renderConversation()
            }
        })
        conversationBody.addView(Button(this@MainActivity).apply {
            text = "Nueva sesión"
            isAllCaps = false
            setOnClickListener { startNewSession() }
        })
        box.addView(card("Conversación", "Control de sesión y contenido local", conversationBody), LinearLayout.LayoutParams(-1, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
            setMargins(0, 0, 0, dp(10))
        })

        val memoryBody = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            addView(TextView(this@MainActivity).apply {
                text = "Sesión local: " + store.sessionId
                textSize = 12f
                setTextColor(Color.rgb(158, 167, 179))
                setPadding(0, 0, 0, dp(8))
            })
            addView(Button(this@MainActivity).apply {
                text = "Recargar contexto"
                isAllCaps = false
                setOnClickListener { loadRemoteContext() }
            })
        }
        box.addView(card("Memoria", "Historial local y contexto de conversación", memoryBody), LinearLayout.LayoutParams(-1, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
            setMargins(0, 0, 0, dp(10))
        })

        val info = TextView(this).apply {
            text = "DEEP33\nVersión: " + BuildConfig.VERSION_NAME + "\nLa configuración modifica la experiencia de usuario; la arquitectura de conectividad permanece intacta."
            textSize = 12f
            setTextColor(Color.rgb(145, 153, 165))
            setPadding(0, dp(2), 0, 0)
        }
        box.addView(card("Información de DEEP33", "Identidad y versión", info))

        scroll.addView(box)
        return scroll
    }
    private fun voicePanelOrNull(): LinearLayout? =
        if (::voiceModeOverlay.isInitialized) voiceModeOverlay else null
    private fun selectPersonality(personality: Personality) {
        store.personality = personality.key
        currentPersonalityView.text = "Modo: " + personality.key
        currentPersonalityView.setTextColor(personality.accent)
        if (::sendButton.isInitialized) sendButton.setTextColor(personality.accent)
        if (::cancelButton.isInitialized) cancelButton.setTextColor(personality.accent)
        if (::micButton.isInitialized) micButton.setTextColor(personality.accent)
        if (::avatarView.isInitialized) avatarView.setPersonality(personality)
        if (::voiceModeOverlay.isInitialized) {
            updateVoiceToneButtons()
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
        if (::voiceModeOverlay.isInitialized) updateVoiceToneButtons()
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
        if (voiceModeActive) {
            showVoiceMode()
            setVoiceState(AvatarState.THINKING)
        }

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
                                if (voiceModeActive) setVoiceState(AvatarState.SPEAKING)
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
        if (voiceModeActive) {
            setVoiceState(AvatarState.IDLE)
            voiceModeOverlay.visibility = View.VISIBLE
        } else if (!success || !store.voiceEnabled || textToSpeech?.isSpeaking != true) {
            if (::avatarView.isInitialized) setVoiceState(AvatarState.IDLE)
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
        if (voiceModeActive) setVoiceState(AvatarState.SPEAKING)
        val speech = text
            .replace(Regex("\\\\[([^]]+)]\\\\(([^)]+)\\\\)"), "$1")
            .replace(Regex("[*_#>]"), "")
            .replace("\u0060", "")
        applyVoiceTone()
        val params = Bundle().apply {
            putFloat(TextToSpeech.Engine.KEY_PARAM_VOLUME, voiceVolume.coerceIn(0.10f, 1f))
        }
        textToSpeech?.speak(
            speech,
            TextToSpeech.QUEUE_FLUSH,
            params,
            "deep33-response-" + System.currentTimeMillis()
        )
    }
    private fun renderConversation() {
        if (!::chatContainer.isInitialized) return
        chatContainer.removeAllViews()

        if (conversation.isEmpty()) {
            showWelcomeState()
            return
        }

        conversation.takeLast(50).forEach { message ->
            appendBubble(
                if (message.role == "user") "TÚ" else "DEEP33",
                message.content,
                if (message.role == "user") Color.rgb(12, 34, 27) else Color.rgb(42, 12, 18)
            )
        }
        chatContainer.post { scrollToBottom() }
    }

    private fun showWelcomeState() {
        val wrap = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER_HORIZONTAL
            setPadding(dp(24), dp(34), dp(24), dp(24))
        }

        wrap.addView(TextView(this).apply {
            text = "DEEP33"
            textSize = 30f
            setTextColor(Color.WHITE)
            setTypeface(typeface, android.graphics.Typeface.BOLD)
            gravity = Gravity.CENTER
        }, LinearLayout.LayoutParams(-1, ViewGroup.LayoutParams.WRAP_CONTENT))

        wrap.addView(TextView(this).apply {
            text = "¿Qué quieres explorar?"
            textSize = 19f
            setTextColor(Color.rgb(190, 198, 208))
            gravity = Gravity.CENTER
            setPadding(0, dp(5), 0, dp(22))
        }, LinearLayout.LayoutParams(-1, ViewGroup.LayoutParams.WRAP_CONTENT))

        val actions = listOf(
            Triple("Explorar una idea", "Ayúdame a explorar esta idea: ", Color.rgb(0, 255, 140)),
            Triple("Analizar algo", "Quiero analizar esto: ", Color.rgb(255, 60, 80)),
            Triple("Buscar información", "Busca información sobre: ", Color.rgb(0, 255, 140)),
            Triple("Conversar", "Quiero conversar sobre: ", Color.rgb(255, 60, 80))
        )

        actions.forEach { (label, prompt, accent) ->
            val button = Button(this).apply {
                text = label
                isAllCaps = false
                textSize = 15f
                gravity = Gravity.START or Gravity.CENTER_VERTICAL
                setTextColor(Color.rgb(230, 234, 239))
                minHeight = dp(58)
                setPadding(dp(18), 0, dp(18), 0)
                setBackground(
                    GradientDrawable().apply {
                        setColor(Color.rgb(15, 18, 23))
                        cornerRadius = dp(17).toFloat()
                        setStroke(dp(1), Color.argb(170, Color.red(accent), Color.green(accent), Color.blue(accent)))
                    }
                )
                setOnClickListener {
                    if (::input.isInitialized) {
                        input.setText(prompt)
                        input.setSelection(input.text.length)
                        input.requestFocus()
                    }
                }
            }
            wrap.addView(button, LinearLayout.LayoutParams(-1, dp(58)).apply {
                setMargins(0, 0, 0, dp(10))
            })
        }

        val hint = TextView(this).apply {
            text = "Escribe tu pregunta abajo para comenzar."
            textSize = 12f
            setTextColor(Color.rgb(108, 116, 128))
            gravity = Gravity.CENTER
            setPadding(0, dp(10), 0, 0)
        }
        wrap.addView(hint, LinearLayout.LayoutParams(-1, ViewGroup.LayoutParams.WRAP_CONTENT))

        chatContainer.addView(
            wrap,
            LinearLayout.LayoutParams(-1, ViewGroup.LayoutParams.WRAP_CONTENT)
        )
        chatContainer.post { scrollToBottom() }
    }

    private fun appendBubble(
        label: String,
        content: String,
        background: Int,
        rich: Boolean = true
    ): TextView {
        val isAssistant = label == "DEEP33"
        val bubble = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(14), dp(12), dp(14), dp(10))
            setBackground(GradientDrawable().apply {
                setColor(background)
                cornerRadius = dp(22).toFloat()
                setStroke(dp(1), if (isAssistant) Color.rgb(150, 34, 50) else Color.rgb(0, 190, 105))
            })
        }

        val labelRow = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        labelRow.addView(TextView(this).apply {
            text = label
            setTextColor(if (isAssistant) Personality.fromKey(store.personality).accent else Color.rgb(172, 181, 193))
            textSize = 11f
            setTypeface(typeface, android.graphics.Typeface.BOLD)
        }, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))

        val contentContainer = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
        }
        val contentView = TextView(this).apply {
            tag = content
            textSize = 16f
            setTextColor(Color.rgb(245, 247, 250))
            setPadding(0, dp(6), 0, 0)
            includeFontPadding = false
            movementMethod = LinkMovementMethod.getInstance()
            maxWidth = (resources.displayMetrics.widthPixels * if (isAssistant) 0.90f else 0.82f).roundToInt()
        }
        contentContainer.addView(contentView)

        if (isAssistant) {
            val copyButton = TextView(this).apply {
                text = "Copiar"
                textSize = 12f
                gravity = Gravity.CENTER
                setTextColor(Color.rgb(178, 187, 198))
                setPadding(dp(8), dp(4), dp(8), dp(4))
                isClickable = true
                isFocusable = true
                contentDescription = "Copiar respuesta"
                setBackground(GradientDrawable().apply {
                    setColor(Color.TRANSPARENT)
                    cornerRadius = dp(14).toFloat()
                })
                setOnClickListener {
                    val clipboard = getSystemService(CLIPBOARD_SERVICE) as ClipboardManager
                    clipboard.setPrimaryClip(ClipData.newPlainText("DEEP33", contentView.tag?.toString().orEmpty()))
                    text = "Copiado"
                    postDelayed({ text = "Copiar" }, 1200L)
                }
            }
            labelRow.addView(copyButton, LinearLayout.LayoutParams(dp(68), dp(34)))
        }

        bubble.addView(labelRow)
        bubble.addView(contentContainer, LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT))

        if (rich && isAssistant && content.contains("\u0060\u0060\u0060")) {
            renderRichMarkdown(contentContainer, content)
        } else {
            renderMarkdown(contentView, content)
        }

        val maxBubbleWidth = (resources.displayMetrics.widthPixels * if (isAssistant) 0.92f else 0.84f).roundToInt().coerceAtLeast(dp(120))
        contentView.maxWidth = maxBubbleWidth
        val params = LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
            gravity = if (isAssistant) Gravity.START else Gravity.END
            setMargins(if (isAssistant) dp(2) else dp(46), 0, if (isAssistant) dp(10) else dp(2), dp(10))
        }
        chatContainer.addView(bubble, params)
        bubble.alpha = 0f
        bubble.translationY = dp(10).toFloat()
        bubble.animate().alpha(1f).translationY(0f).setDuration(180L).setInterpolator(android.view.animation.DecelerateInterpolator()).start()
        addPressFeedback(bubble)
        bubble.post { scrollToBottom() }
        return contentView
    }

    private fun replaceActiveBubbleWithRich(content: String) {
        val current = activeBubble ?: return
        val bubble = current.parent?.parent as? ViewGroup
        val parent = bubble?.parent as? ViewGroup
        if (bubble != null && parent != null) parent.removeView(bubble)
        appendBubble("DEEP33", content, Color.rgb(42, 12, 18), rich = true)
    }

    private fun renderRichMarkdown(container: LinearLayout, markdown: String) {
        container.removeAllViews()
        val normalized = markdown.replace("\r\n", "\n").replace("\r", "\n")
        val fence = "\u0060\u0060\u0060"
        val regex = Regex("(?s)" + Regex.escape(fence) + "([^\\n]*)\\n(.*?)" + Regex.escape(fence))
        var cursor = 0

        fun addTextSegment(segment: String) {
            if (segment.isBlank()) return
            val tv = TextView(this).apply {
                textSize = 16f
                setTextColor(Color.rgb(245, 247, 250))
                includeFontPadding = false
                movementMethod = LinkMovementMethod.getInstance()
                maxWidth = (resources.displayMetrics.widthPixels * 0.90f).roundToInt()
            }
            renderMarkdown(tv, segment.trimEnd())
            container.addView(tv, LinearLayout.LayoutParams(-1, ViewGroup.LayoutParams.WRAP_CONTENT))
        }

        regex.findAll(normalized).forEach { match ->
            addTextSegment(normalized.substring(cursor, match.range.first))
            addCodeBlock(container, match.groupValues[1].trim(), match.groupValues[2].trimEnd('\n'))
            cursor = match.range.last + 1
        }
        addTextSegment(normalized.substring(cursor))
    }

    private fun addCodeBlock(container: LinearLayout, language: String, code: String) {
        val card = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(10), dp(8), dp(10), dp(8))
            setBackground(GradientDrawable().apply {
                setColor(Color.rgb(9, 12, 16))
                cornerRadius = dp(14).toFloat()
                setStroke(dp(1), Color.rgb(48, 61, 66))
            })
        }
        val header = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        header.addView(TextView(this).apply {
            text = language.ifBlank { "código" }.lowercase(Locale.getDefault())
            textSize = 11f
            setTextColor(Color.rgb(0, 230, 150))
            setTypeface(typeface, android.graphics.Typeface.BOLD)
        }, LinearLayout.LayoutParams(0, dp(30), 1f))
        val copy = TextView(this).apply {
            text = "Copiar"
            textSize = 11f
            gravity = Gravity.CENTER
            setTextColor(Color.rgb(178, 187, 198))
            isClickable = true
            isFocusable = true
            setOnClickListener {
                val clipboard = getSystemService(CLIPBOARD_SERVICE) as ClipboardManager
                clipboard.setPrimaryClip(ClipData.newPlainText("DEEP33 código", code))
                text = "Copiado"
                postDelayed({ text = "Copiar" }, 1200L)
            }
        }
        header.addView(copy, LinearLayout.LayoutParams(dp(68), dp(30)))
        card.addView(header)

        val horizontal = android.widget.HorizontalScrollView(this).apply {
            setHorizontalScrollBarEnabled(false)
        }
        val codeText = TextView(this).apply {
            text = code
            textSize = 13f
            setTextColor(Color.rgb(236, 240, 244))
            typeface = android.graphics.Typeface.MONOSPACE
            setPadding(dp(4), dp(6), dp(4), dp(8))
            setHorizontallyScrolling(true)
        }
        horizontal.addView(codeText, ViewGroup.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT))
        card.addView(horizontal, LinearLayout.LayoutParams(-1, ViewGroup.LayoutParams.WRAP_CONTENT))
        container.addView(card, LinearLayout.LayoutParams(-1, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
            setMargins(0, dp(8), 0, dp(8))
        })
    }

    private fun renderMarkdown(view: TextView, markdown: String) {
        view.text = MarkdownRenderer.render(markdown)
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
            AvatarState.IDLE -> "Listo"
            AvatarState.LISTENING -> "Escuchando"
            AvatarState.THINKING -> "Pensando"
            AvatarState.SPEAKING -> "Hablando"
        }
    }

    private fun showVoiceMode() {
        if (!::voiceModeOverlay.isInitialized) return
        voiceModeActive = true
        voiceModeOverlay.animate().cancel()
        voiceModeOverlay.visibility = View.VISIBLE
        voiceModeOverlay.alpha = 0f
        voiceModeOverlay.translationY = dp(18).toFloat()
        voiceModeOverlay.animate()
            .alpha(1f)
            .translationY(0f)
            .setDuration(220L)
            .setInterpolator(android.view.animation.DecelerateInterpolator())
            .start()
        updateVoiceToneButtons()
    }

    private fun hideVoiceMode() {
        if (!::voiceModeOverlay.isInitialized) return
        stopVoiceInput()
        textToSpeech?.stop()
        if (activeTask?.isDone == false) cancelGeneration()
        voiceModeActive = false
        setVoiceState(AvatarState.IDLE)
        voiceModeOverlay.animate().cancel()
        voiceModeOverlay.animate()
            .alpha(0f)
            .translationY(dp(18).toFloat())
            .setDuration(170L)
            .withEndAction {
                voiceModeOverlay.visibility = View.GONE
                voiceModeOverlay.alpha = 1f
                voiceModeOverlay.translationY = 0f
            }
            .start()
    }

    private fun buildVoiceModeOverlay(): LinearLayout {
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER_HORIZONTAL
            setPadding(dp(22), dp(36), dp(22), dp(24))
            setBackgroundColor(Color.rgb(5, 6, 7))
            isClickable = true
            isFocusable = true
        }

        val top = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        top.addView(TextView(this).apply {
            text = "DEEP33 · VOZ"
            textSize = 18f
            setTextColor(Color.WHITE)
            setTypeface(typeface, android.graphics.Typeface.BOLD)
        }, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        top.addView(Button(this).apply {
            text = "Cerrar"
            isAllCaps = false
            setOnClickListener { hideVoiceMode() }
        }, LinearLayout.LayoutParams(dp(82), dp(44)))
        root.addView(top)

        voiceStateView = TextView(this).apply {
            text = "Listo"
            textSize = 17f
            gravity = Gravity.CENTER
            setTextColor(Color.rgb(0, 255, 140))
            setPadding(0, dp(16), 0, dp(8))
        }
        root.addView(voiceStateView, LinearLayout.LayoutParams(-1, ViewGroup.LayoutParams.WRAP_CONTENT))

        avatarView = VoiceAvatarView(this).apply {
            setPersonality(Personality.fromKey(store.personality))
            setVoiceState(AvatarState.IDLE)
            contentDescription = "Avatar de voz de DEEP33"
        }
        root.addView(avatarView, LinearLayout.LayoutParams(dp(270), dp(270)))

        root.addView(TextView(this).apply {
            text = "TONO DE VOZ"
            textSize = 11f
            setTextColor(Color.rgb(125, 133, 145))
            setPadding(0, dp(2), 0, dp(7))
        }, LinearLayout.LayoutParams(-1, ViewGroup.LayoutParams.WRAP_CONTENT))

        val tones = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER
        }
        VoiceTone.entries.forEach { tone ->
            val button = Button(this).apply {
                tag = tone
                text = tone.label
                isAllCaps = false
                textSize = 12f
                minHeight = dp(44)
                setOnClickListener { selectVoiceTone(tone) }
            }
            voiceToneButtons[tone] = button
            tones.addView(button, LinearLayout.LayoutParams(0, dp(44), 1f).apply {
                setMargins(dp(3), 0, dp(3), 0)
            })
        }
        root.addView(tones, LinearLayout.LayoutParams(-1, dp(50)))

        val volumeLabel = TextView(this).apply {
            tag = "VOICE_VOLUME_LABEL"
            text = "VOLUMEN · 85%"
            textSize = 11f
            setTextColor(Color.rgb(125, 133, 145))
            setPadding(0, dp(12), 0, dp(3))
        }
        root.addView(volumeLabel, LinearLayout.LayoutParams(-1, ViewGroup.LayoutParams.WRAP_CONTENT))

        root.addView(SeekBar(this).apply {
            max = 100
            progress = (voiceVolume * 100f).roundToInt()
            setOnSeekBarChangeListener(object : SeekBar.OnSeekBarChangeListener {
                override fun onProgressChanged(seekBar: SeekBar?, progress: Int, fromUser: Boolean) {
                    voiceVolume = (progress.coerceAtLeast(10) / 100f).coerceIn(0.10f, 1f)
                    volumeLabel.text = "VOLUMEN · ${(voiceVolume * 100f).roundToInt()}%"
                }
                override fun onStartTrackingTouch(seekBar: SeekBar?) = Unit
                override fun onStopTrackingTouch(seekBar: SeekBar?) = Unit
            })
        }, LinearLayout.LayoutParams(-1, dp(42)))

        root.addView(Button(this).apply {
            text = "TOCAR PARA HABLAR"
            isAllCaps = false
            textSize = 15f
            minHeight = dp(56)
            setTextColor(Color.WHITE)
            setBackground(GradientDrawable().apply {
                setColor(Color.rgb(19, 24, 21))
                cornerRadius = dp(28).toFloat()
                setStroke(dp(1), Color.rgb(0, 255, 140))
            })
            setOnClickListener { toggleVoiceInput() }
        }, LinearLayout.LayoutParams(-1, dp(56)).apply {
            setMargins(0, dp(10), 0, 0)
        })

        updateVoiceToneButtons()
        return root
    }

    private fun updateVoiceToneButtons() {
        voiceToneButtons.forEach { (tone, button) ->
            val selected = tone == voiceTone
            button.setTextColor(if (selected) Color.WHITE else Color.rgb(185, 191, 200))
            button.setBackground(GradientDrawable().apply {
                setColor(if (selected) Color.rgb(40, 17, 22) else Color.rgb(15, 18, 22))
                cornerRadius = dp(16).toFloat()
                setStroke(dp(1), if (selected) Color.rgb(255, 42, 68) else Color.rgb(48, 55, 65))
            })
        }
    }

    private fun selectVoiceTone(tone: VoiceTone) {
        voiceTone = tone
        applyVoiceTone()
        updateVoiceToneButtons()
    }

    private fun applyVoiceTone() {
        textToSpeech?.setSpeechRate(voiceTone.rate)
        textToSpeech?.setPitch(voiceTone.pitch)
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
        showVoiceMode()
        setVoiceState(AvatarState.LISTENING)
        speechListening = true

        val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
            putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            putExtra(RecognizerIntent.EXTRA_LANGUAGE, "es-CL")
            putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
            putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 1)
        }
        speechRecognizer?.startListening(intent)
    }

    private fun stopVoiceInput() {
        if (!speechListening) {
            if (voiceModeActive) setVoiceState(AvatarState.IDLE)
            return
        }
        speechListening = false
        speechRecognizer?.stopListening()
        setVoiceState(AvatarState.IDLE)
    }

    private val recognitionListener = object : RecognitionListener {
        override fun onReadyForSpeech(params: Bundle?) {
            speechListening = true
            if (voiceModeActive) setVoiceState(AvatarState.LISTENING)
        }

        override fun onBeginningOfSpeech() {
            if (voiceModeActive) setVoiceState(AvatarState.LISTENING)
        }

        override fun onRmsChanged(rmsdB: Float) {
            if (::avatarView.isInitialized) avatarView.setAudioLevel((rmsdB / 10f).coerceIn(0f, 1f))
        }

        override fun onBufferReceived(buffer: ByteArray?) = Unit

        override fun onEndOfSpeech() {
            speechListening = false
            if (voiceModeActive) setVoiceState(AvatarState.THINKING)
        }

        override fun onError(error: Int) {
            speechListening = false
            if (voiceModeActive) setVoiceState(AvatarState.IDLE)
        }

        override fun onResults(results: Bundle?) {
            speechListening = false
            val recognized = results
                ?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                ?.firstOrNull()
                .orEmpty()
                .trim()
            if (recognized.isNotBlank()) {
                sendMessage(recognized)
            } else if (voiceModeActive) {
                setVoiceState(AvatarState.IDLE)
            }
        }

        override fun onPartialResults(partialResults: Bundle?) = Unit
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
