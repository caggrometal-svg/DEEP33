package cl.caggrometal.deep33

import android.app.Activity
import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.os.Bundle
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.view.Window
import android.view.WindowInsets
import android.widget.Space
import android.widget.Button
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import java.util.concurrent.Executors
import java.util.concurrent.Future
import java.util.Locale
import android.speech.tts.TextToSpeech
import java.util.concurrent.atomic.AtomicBoolean

private enum class ConnectionState { CONNECTING, ONLINE, OFFLINE }

class MainActivity : Activity() {
    private lateinit var statusView: TextView
    private lateinit var contentFrame: FrameLayout
    private lateinit var chatContainer: LinearLayout
    private lateinit var input: EditText
    private lateinit var sendButton: Button
    private lateinit var cancelButton: Button
    private lateinit var diagnosticsView: TextView

    private val executor = Executors.newFixedThreadPool(2)
    private lateinit var store: SessionStore
    private val conversation = mutableListOf<UiMessage>()
    private var activeTask: Future<*>? = null
    private var activeBubble: TextView? = null
    private val cancelRequested = AtomicBoolean(false)
    private var textToSpeech: TextToSpeech? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        store = SessionStore(this)
        conversation.addAll(store.loadMessages())
        textToSpeech = TextToSpeech(this) { status ->
            if (status == TextToSpeech.SUCCESS) {
                val result = textToSpeech?.setLanguage(Locale("es", "CL"))
                if (result == TextToSpeech.LANG_MISSING_DATA || result == TextToSpeech.LANG_NOT_SUPPORTED) {
                    textToSpeech?.language = Locale("es")
                }
            }
        }
        window.statusBarColor = Color.rgb(8, 10, 15)
        window.navigationBarColor = Color.rgb(8, 10, 15)
        window.decorView.setOnApplyWindowInsetsListener { view, insets ->
            val bars = insets.getInsets(WindowInsets.Type.systemBars())
            view.setPadding(bars.left, bars.top, bars.right, bars.bottom)
            insets
        }
        setContentView(buildRoot())
        renderConversation()
        showTab(Tab.CHAT)
        applyPersonalityTheme(Personality.fromKey(store.personality))
        checkConnectivity()
        loadRemoteContext()
    }

    override fun onDestroy() {
        cancelRequested.set(true)
        Deep33Api.cancelActiveStream()
        activeTask?.cancel(true)
        executor.shutdownNow()
        textToSpeech?.stop()
        textToSpeech?.shutdown()
        textToSpeech = null
        super.onDestroy()
    }

    private enum class Tab { CHAT, STATUS, SETTINGS }

    private fun buildRoot(): View {
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(Color.rgb(8, 10, 15))
            setPadding(16, 10, 16, 8)
        }

        val header = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(6, 4, 6, 14)
        }
        val title = TextView(this).apply {
            text = "DEEP33"
            setTextColor(Color.WHITE)
            textSize = 25f
            gravity = Gravity.CENTER_HORIZONTAL
        }
        statusView = TextView(this).apply {
            text = "CONECTANDO · comprobando backend..."
            setTextColor(Color.rgb(255, 193, 7))
            textSize = 13f
            gravity = Gravity.CENTER_HORIZONTAL
            setPadding(0, 6, 0, 0)
        }
        header.addView(title)
        header.addView(statusView)
        root.addView(header, ViewGroup.LayoutParams(-1, ViewGroup.LayoutParams.WRAP_CONTENT))

        contentFrame = FrameLayout(this)
        root.addView(contentFrame, LinearLayout.LayoutParams(-1, 0, 1f))
        root.addView(buildNavigation(), ViewGroup.LayoutParams(-1, ViewGroup.LayoutParams.WRAP_CONTENT))
        return root
    }

    private fun buildNavigation(): View {
        val nav = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER
            setPadding(0, 6, 0, 2)
        }
        nav.addView(navButton("CHAT") { showTab(Tab.CHAT) }, weightParams())
        nav.addView(navButton("ESTADO") { showTab(Tab.STATUS) }, weightParams())
        nav.addView(navButton("CONFIGURACIÓN") { showTab(Tab.SETTINGS) }, weightParams())
        return nav
    }

    private fun weightParams() =
        LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply {
            setMargins(4, 0, 4, 0)
        }

    private fun navButton(label: String, action: () -> Unit): Button =
        Button(this).apply {
            text = label
            textSize = 10f
            minHeight = 52
            isAllCaps = false
            setOnClickListener { action() }
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
        val scroll = ScrollView(this).apply {
            isFillViewport = true
            addView(LinearLayout(this@MainActivity).also { chatContainer = it })
        }
        box.addView(scroll, LinearLayout.LayoutParams(-1, 0, 1f))

        input = EditText(this).apply {
            hint = "Escribe un mensaje"
            setHintTextColor(Color.GRAY)
            setTextColor(Color.WHITE)
            textSize = 16f
            maxLines = 4
            setBackgroundColor(Color.rgb(18, 22, 30))
            setPadding(16, 12, 16, 12)
        }
        sendButton = Button(this).apply {
            text = "ENVIAR"
            setOnClickListener { sendMessage() }
        }
        cancelButton = Button(this).apply {
            text = "CANCELAR"
            visibility = View.GONE
            setOnClickListener { cancelGeneration() }
        }

        val composer = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.BOTTOM
            setPadding(0, 8, 0, 0)
            addView(input, LinearLayout.LayoutParams(0, 58, 1f).apply { setMargins(0, 0, 8, 0) })
            addView(sendButton, LinearLayout.LayoutParams(82, 58).apply { setMargins(0, 0, 6, 0) })
            addView(cancelButton, LinearLayout.LayoutParams(82, 58))
        }
        box.addView(composer)
        return box
    }

    private fun buildStatus(): View {
        val box = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(10, 10, 10, 10)
        }
        diagnosticsView = TextView(this).apply {
            text = "Consultando diagnóstico..."
            setTextColor(Color.LTGRAY)
            textSize = 15f
            setPadding(16, 16, 16, 16)
        }
        val retry = Button(this).apply {
            text = "COMPROBAR DE NUEVO"
            setOnClickListener { checkConnectivity() }
        }
        box.addView(diagnosticsView, ViewGroup.LayoutParams(-1, ViewGroup.LayoutParams.WRAP_CONTENT))
        box.addView(retry, ViewGroup.LayoutParams(-1, ViewGroup.LayoutParams.WRAP_CONTENT))
        return box
    }

    private fun buildSettings(): View {
        val box = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(10, 10, 10, 10)
        }
        val sessionView = TextView(this).apply {
            text = "Sesión: " + store.sessionId
            setTextColor(Color.LTGRAY)
            textSize = 13f
            setPadding(16, 16, 16, 16)
        }
        val voiceButton = Button(this).apply {
            text = if (store.voiceEnabled) "VOZ: ACTIVADA" else "VOZ: DESACTIVADA"
            setOnClickListener {
                store.voiceEnabled = !store.voiceEnabled
                text = if (store.voiceEnabled) "VOZ: ACTIVADA" else "VOZ: DESACTIVADA"
                if (!store.voiceEnabled) textToSpeech?.stop()
            }
        }
        val personalityTitle = TextView(this).apply {
            text = "PERSONALIDAD: " + store.personality
            setTextColor(Personality.fromKey(store.personality).accent)
            textSize = 15f
            setPadding(16, 12, 16, 8)
        }
        val personalityRow = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
        }
        Personality.entries.forEach { option ->
            personalityRow.addView(Button(this).apply {
                text = option.key + " · " + option.description
                setTextColor(option.accent)
                setOnClickListener {
                    selectPersonality(option)
                    personalityTitle.text = "PERSONALIDAD: " + option.key
                    showTab(Tab.CHAT)
                }
            })
        }
        val clearConversation = Button(this).apply {
            text = "BORRAR CONVERSACIÓN"
            setOnClickListener {
                conversation.clear()
                store.clearConversation()
                showTab(Tab.CHAT)
            }
        }
        val newSession = Button(this).apply {
            text = "NUEVA SESIÓN"
            setOnClickListener {
                cancelGeneration()
                store.resetSession()
                conversation.clear()
                showTab(Tab.CHAT)
                checkConnectivity()
            }
        }
        box.addView(sessionView)
        box.addView(voiceButton, ViewGroup.LayoutParams(-1, ViewGroup.LayoutParams.WRAP_CONTENT))
        box.addView(personalityTitle, ViewGroup.LayoutParams(-1, ViewGroup.LayoutParams.WRAP_CONTENT))
        box.addView(personalityRow, ViewGroup.LayoutParams(-1, ViewGroup.LayoutParams.WRAP_CONTENT))
        box.addView(clearConversation, ViewGroup.LayoutParams(-1, ViewGroup.LayoutParams.WRAP_CONTENT))
        box.addView(newSession, ViewGroup.LayoutParams(-1, ViewGroup.LayoutParams.WRAP_CONTENT))
        return box
    }

    private fun selectPersonality(personality: Personality) {
        store.personality = personality.key
        applyPersonalityTheme(personality)
        syncPreferences()
    }

    private fun applyPersonalityTheme(personality: Personality) {
        statusView.setTextColor(personality.accent)
        if (::sendButton.isInitialized) sendButton.setTextColor(personality.accent)
        if (::cancelButton.isInitialized) cancelButton.setTextColor(personality.accent)
    }

    private fun syncPreferences() {
        val personality = store.personality
        executor.submit {
            try {
                Deep33Api.setPreferences(store.sessionId, personality)
            } catch (_: Exception) {
                // Remote persistence is retried on the next preference change.
            }
        }
    }

    private fun loadRemoteContext() {
        executor.submit {
            try {
                val remote = Deep33Api.memoryContext(store.sessionId)
                val session = remote.optJSONObject("session")
                val remotePersonality = session?.optString("personality").orEmpty()
                if (remotePersonality.isNotBlank()) store.personality = remotePersonality
                val messages = remote.optJSONArray("messages") ?: return@submit
                val remoteMessages = buildList {
                    for (i in 0 until messages.length()) {
                        val item = messages.optJSONObject(i) ?: continue
                        val role = item.optString("role")
                        val content = item.optString("content")
                        if (role.isNotBlank() && content.isNotBlank()) {
                            add(UiMessage(role, content))
                        }
                    }
                }

                val merged = mutableListOf<UiMessage>()
                val seen = mutableSetOf<Pair<String, String>>()
                (remoteMessages + conversation.takeLast(50)).forEach { item ->
                    if (seen.add(item.role to item.content)) merged.add(item)
                }

                conversation.clear()
                conversation.addAll(merged.takeLast(50))
                store.saveMessages(conversation)
                runOnUiThread { renderConversation() }
            } catch (_: Exception) {
                // Keep local cache when remote memory is temporarily unavailable.
            }
        }
    }

    private fun checkConnectivity() {
        updateConnection(ConnectionState.CONNECTING)
        executor.submit {
            try {
                val health = Deep33Api.get("/health", store.sessionId)
                val diagnostics = Deep33Api.get("/v1/ai/diagnostics", store.sessionId)
                val online = health.optString("status") == "PASS" && diagnostics.optBoolean("online", false)
                val gateway = diagnostics.optJSONObject("gateway")
                val provider = gateway?.optString("provider", "") ?: ""
                val model = gateway?.optString("model", "") ?: ""
                val summary = if (online) {
                    "ONLINE\nBACKEND: PASS\nAI GATEWAY: PASS\nPROVIDER: " + provider + "\nMODEL: " + model
                } else {
                    "OFFLINE\nRevisa conectividad y diagnóstico del backend."
                }
                runOnUiThread {
                    updateConnection(if (online) ConnectionState.ONLINE else ConnectionState.OFFLINE)
                    if (::diagnosticsView.isInitialized) diagnosticsView.text = summary
                }
            } catch (e: Deep33ApiException) {
                runOnUiThread {
                    updateConnection(ConnectionState.OFFLINE)
                    if (::diagnosticsView.isInitialized) diagnosticsView.text = "OFFLINE\n" + (e.message ?: "Error de conectividad.")
                }
            } catch (_: Exception) {
                runOnUiThread {
                    updateConnection(ConnectionState.OFFLINE)
                    if (::diagnosticsView.isInitialized) diagnosticsView.text = "OFFLINE\nError inesperado de conectividad."
                }
            }
        }
    }

    private fun sendMessage() {
        val text = input.text.toString().trim()
        if (text.isEmpty() || activeTask?.isDone == false) return

        conversation.add(UiMessage("user", text))
        store.saveMessages(conversation)
        appendBubble("TÚ", text, Color.rgb(30, 38, 50))
        input.setText("")

        activeBubble = appendBubble("DEEP33", "Pensando…", Color.rgb(12, 35, 40))
        cancelRequested.set(false)
        sendButton.isEnabled = false
        input.isEnabled = false
        cancelButton.visibility = View.VISIBLE
        updateConnection(ConnectionState.CONNECTING)

        val payload = org.json.JSONArray()
        conversation.takeLast(50).forEach {
            payload.put(org.json.JSONObject().put("role", it.role).put("content", it.content))
        }

        activeTask = executor.submit {
            try {
                val finalText = try {
                    Deep33Api.stream(
                    payload,
                    store.sessionId,
                    store.personality,
                    isCancelled = { cancelRequested.get() || Thread.currentThread().isInterrupted },
                    onText = { chunk ->
                        runOnUiThread {
                            val bubble = activeBubble ?: return@runOnUiThread
                            val current = bubble.text.toString()
                            val existing = current.substringAfter("\n", "")
                            val next = if (existing == "Pensando…" || existing == "Generación cancelada.") chunk else existing + chunk
                            bubble.text = "DEEP33\n" + next
                        }
                    }
                    )
                } catch (streamError: Deep33ApiException) {
                    if (streamError.kind == Deep33ApiException.Kind.CANCELLED) throw streamError
                    val fallback = Deep33Api.generate(payload, store.sessionId, store.personality)
                    fallback.optString("text").ifBlank { fallback.optJSONObject("result")?.optString("text").orEmpty() }
                }

                if (finalText.isBlank()) throw Deep33ApiException(Deep33ApiException.Kind.BAD_RESPONSE)

                conversation.add(UiMessage("assistant", finalText))
                store.saveMessages(conversation)

                runOnUiThread {
                    speakAssistant(finalText)
                    cleanupGeneration(true)
                }
            } catch (e: Deep33ApiException) {
                if (e.kind == Deep33ApiException.Kind.CANCELLED || cancelRequested.get()) {
                    runOnUiThread {
                        activeBubble?.text = "DEEP33\nGeneración cancelada."
                        cleanupGeneration(false)
                    }
                } else {
                    runOnUiThread {
                        activeBubble?.text = "ERROR\n" + (e.message ?: "Error de comunicación.")
                        cleanupGeneration(false)
                    }
                }
            } catch (_: Exception) {
                if (cancelRequested.get()) {
                    runOnUiThread {
                        activeBubble?.text = "DEEP33\nGeneración cancelada."
                        cleanupGeneration(false)
                    }
                } else {
                    runOnUiThread {
                        activeBubble?.text = "ERROR\nError inesperado de comunicación."
                        cleanupGeneration(false)
                    }
                }
            }
        }
    }

    private fun cancelGeneration() {
        if (activeTask?.isDone != false) return
        cancelRequested.set(true)
        Deep33Api.cancelActiveStream()
        activeTask?.cancel(true)
        activeBubble?.text = "DEEP33\nGeneración cancelada."
        cleanupGeneration(false)
    }

    private fun cleanupGeneration(success: Boolean) {
        activeTask = null
        activeBubble = null
        sendButton.isEnabled = true
        input.isEnabled = true
        cancelButton.visibility = View.GONE
        updateConnection(if (success) ConnectionState.ONLINE else ConnectionState.OFFLINE)
    }

    private fun updateConnection(state: ConnectionState) {
        val (text, color) = when (state) {
            ConnectionState.CONNECTING -> "GENERANDO · DEEP33" to Color.rgb(255, 193, 7)
            ConnectionState.ONLINE -> "ONLINE · DEEP33" to Color.rgb(0, 255, 140)
            ConnectionState.OFFLINE -> "OFFLINE · DEEP33" to Color.rgb(255, 80, 80)
        }
        statusView.text = text
        statusView.setTextColor(color)
    }

    private fun speakAssistant(text: String) {
        if (!store.voiceEnabled || text.isBlank()) return
        textToSpeech?.speak(text, TextToSpeech.QUEUE_FLUSH, null, "deep33-response")
    }

    private fun renderConversation() {
        if (!::chatContainer.isInitialized) return
        chatContainer.removeAllViews()
        conversation.takeLast(50).forEach { message ->
            appendBubble(
                if (message.role == "user") "TÚ" else "DEEP33",
                message.content,
                if (message.role == "user") Color.rgb(30, 38, 50) else Color.rgb(12, 35, 40)
            )
        }
    }

    private fun appendBubble(label: String, content: String, background: Int): TextView {
        val isAssistant = label == "DEEP33"
        val accent = Personality.fromKey(store.personality).accent
        val bubble = TextView(this).apply {
            text = label + "\n" + content
            setTextColor(Color.WHITE)
            textSize = 16f
            setPadding(18, 14, 18, 14)
            gravity = Gravity.START
            includeFontPadding = false
            setBackground(
                GradientDrawable().apply {
                    setColor(background)
                    cornerRadius = 24f
                    if (isAssistant) setStroke(2, accent)
                }
            )
        }
        val params = LinearLayout.LayoutParams(-1, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
            setMargins(0, 0, 0, 12)
        }
        chatContainer.addView(bubble, params)
        chatContainer.post {
            (chatContainer.parent as? ScrollView)?.fullScroll(View.FOCUS_DOWN)
        }
        return bubble
    }
}
