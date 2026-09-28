package cl.caggrometal.deep33

import android.app.Activity
import android.graphics.Color
import android.os.Bundle
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import java.util.concurrent.Executors

private enum class ConnectionState {
    CONNECTING, ONLINE, OFFLINE
}

class MainActivity : Activity() {
    private lateinit var statusView: TextView
    private lateinit var contentFrame: FrameLayout
    private lateinit var chatContainer: LinearLayout
    private lateinit var input: EditText
    private lateinit var sendButton: Button
    private lateinit var diagnosticsView: TextView

    private val executor = Executors.newFixedThreadPool(2)
    private lateinit var store: SessionStore
    private val conversation = mutableListOf<UiMessage>()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        store = SessionStore(this)
        conversation.addAll(store.loadMessages())

        setContentView(buildRoot())
        renderConversation()
        showTab(Tab.CHAT)
        checkConnectivity()
    }

    override fun onDestroy() {
        executor.shutdownNow()
        super.onDestroy()
    }

    private enum class Tab { CHAT, STATUS, SETTINGS }

    private fun buildRoot(): View {
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(Color.rgb(8, 10, 15))
            setPadding(18, 18, 18, 12)
        }

        val header = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(6, 4, 6, 14)
        }

        val title = TextView(this).apply {
            text = "DEEP33"
            setTextColor(Color.WHITE)
            textSize = 28f
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
            setPadding(0, 10, 0, 0)
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
            textSize = 11f
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
        val box = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
        }

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

        val composer = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.BOTTOM
            setPadding(0, 10, 0, 0)
            addView(input, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
            addView(sendButton, LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT))
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
            text = "Sesión: ${store.sessionId}"
            setTextColor(Color.LTGRAY)
            textSize = 13f
            setPadding(16, 16, 16, 16)
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
                store.resetSession()
                conversation.clear()
                showTab(Tab.CHAT)
                checkConnectivity()
            }
        }

        box.addView(sessionView)
        box.addView(clearConversation, ViewGroup.LayoutParams(-1, ViewGroup.LayoutParams.WRAP_CONTENT))
        box.addView(newSession, ViewGroup.LayoutParams(-1, ViewGroup.LayoutParams.WRAP_CONTENT))
        return box
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
                    "ONLINE\nBACKEND: PASS\nAI GATEWAY: PASS\nPROVIDER: $provider\nMODEL: $model"
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
                    if (::diagnosticsView.isInitialized) diagnosticsView.text = "OFFLINE\n${e.message}"
                }
            } catch (_: Exception) {
                runOnUiThread {
                    updateConnection(ConnectionState.OFFLINE)
                    if (::diagnosticsView.isInitialized) diagnosticsView.text =
                        "OFFLINE\nError inesperado de conectividad."
                }
            }
        }
    }

    private fun updateConnection(state: ConnectionState) {
        val (text, color) = when (state) {
            ConnectionState.CONNECTING -> "CONECTANDO · DEEP33" to Color.rgb(255, 193, 7)
            ConnectionState.ONLINE -> "ONLINE · DEEP33" to Color.rgb(0, 255, 140)
            ConnectionState.OFFLINE -> "OFFLINE · DEEP33" to Color.rgb(255, 80, 80)
        }
        statusView.text = text
        statusView.setTextColor(color)
    }

    private fun sendMessage() {
        val text = input.text.toString().trim()
        if (text.isEmpty() || !sendButton.isEnabled) return

        conversation.add(UiMessage("user", text))
        store.saveMessages(conversation)
        appendBubble("TÚ", text, Color.rgb(30, 38, 50))
        input.setText("")
        sendButton.isEnabled = false
        updateConnection(ConnectionState.CONNECTING)

        val payload = org.json.JSONArray()
        conversation.takeLast(50).forEach {
            payload.put(org.json.JSONObject().put("role", it.role).put("content", it.content))
        }

        executor.submit {
            try {
                val response = Deep33Api.generate(payload, store.sessionId)
                val result = response.optJSONObject("result")
                val answer = result?.optString("text").orEmpty()
                    .ifBlank {
                        response.optJSONObject("response")
                            ?.optJSONArray("choices")
                            ?.optJSONObject(0)
                            ?.optJSONObject("message")
                            ?.optString("content").orEmpty()
                    }
                if (answer.isBlank()) throw Deep33ApiException(Deep33ApiException.Kind.BAD_RESPONSE)

                conversation.add(UiMessage("assistant", answer))
                store.saveMessages(conversation)

                runOnUiThread {
                    appendBubble("DEEP33", answer, Color.rgb(12, 35, 40))
                    sendButton.isEnabled = true
                    updateConnection(ConnectionState.ONLINE)
                }
            } catch (e: Deep33ApiException) {
                runOnUiThread {
                    appendBubble("ERROR", e.message ?: "Error de comunicación.", Color.rgb(55, 20, 25))
                    sendButton.isEnabled = true
                    updateConnection(ConnectionState.OFFLINE)
                }
            } catch (_: Exception) {
                runOnUiThread {
                    appendBubble("ERROR", "Error inesperado de comunicación.", Color.rgb(55, 20, 25))
                    sendButton.isEnabled = true
                    updateConnection(ConnectionState.OFFLINE)
                }
            }
        }
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

    private fun appendBubble(label: String, content: String, background: Int) {
        if (!::chatContainer.isInitialized) return
        val bubble = TextView(this).apply {
            text = "$label\n$content"
            setTextColor(Color.WHITE)
            textSize = 16f
            setPadding(18, 14, 18, 14)
            setBackgroundColor(background)
        }
        val params = LinearLayout.LayoutParams(-1, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
            setMargins(0, 0, 0, 12)
        }
        chatContainer.addView(bubble, params)
        chatContainer.post {
            (chatContainer.parent as? ScrollView)?.fullScroll(View.FOCUS_DOWN)
        }
    }
}
