package cl.caggrometal.deep33

import android.app.Activity
import android.graphics.Color
import android.os.Bundle
import android.view.Gravity
import android.view.ViewGroup
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedReader
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.Executors

private object Deep33Api {
    private const val BASE_URL = "https://deep33-backend.onrender.com"
    private val executor = Executors.newCachedThreadPool()

    fun get(path: String): JSONObject {
        val connection = open("GET", path)
        return try {
            val code = connection.responseCode
            val body = readBody(connection)
            if (code !in 200..299) error("HTTP $code")
            JSONObject(body)
        } finally { connection.disconnect() }
    }

    fun chat(messages: JSONArray): JSONObject {
        val connection = open("POST", "/v1/chat")
        return try {
            connection.setRequestProperty("Content-Type", "application/json")
            connection.doOutput = true
            val payload = JSONObject().put("messages", messages)
            connection.outputStream.use { it.write(payload.toString().toByteArray(Charsets.UTF_8)) }
            val code = connection.responseCode
            val body = readBody(connection)
            if (code !in 200..299) error("HTTP $code")
            JSONObject(body)
        } finally { connection.disconnect() }
    }

    fun submit(task: () -> Unit) { executor.submit(task) }

    private fun open(method: String, path: String): HttpURLConnection =
        (URL(BASE_URL + path).openConnection() as HttpURLConnection).apply {
            requestMethod = method
            connectTimeout = 15000
            readTimeout = 90000
            useCaches = false
            setRequestProperty("Accept", "application/json")
        }

    private fun readBody(connection: HttpURLConnection): String {
        val stream = if (connection.responseCode in 200..299) connection.inputStream else connection.errorStream
        return stream.bufferedReader(Charsets.UTF_8).use(BufferedReader::readText)
    }
}

class MainActivity : Activity() {
    private lateinit var status: TextView
    private lateinit var chatContainer: LinearLayout
    private lateinit var input: EditText
    private val messages = JSONArray()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(Color.rgb(8, 10, 15))
            setPadding(24, 24, 24, 18)
        }
        val title = TextView(this).apply {
            text = "DEEP33"
            setTextColor(Color.WHITE)
            textSize = 28f
            gravity = Gravity.CENTER_HORIZONTAL
            setPadding(0, 0, 0, 16)
        }
        status = TextView(this).apply {
            text = "DEEP33 · verificando conectividad..."
            setTextColor(Color.rgb(0, 229, 255))
            textSize = 14f
            setPadding(0, 0, 0, 16)
        }
        chatContainer = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        val scroll = ScrollView(this).apply { addView(chatContainer) }
        input = EditText(this).apply {
            hint = "Escribe un mensaje"
            hintTextColor = Color.GRAY
            setTextColor(Color.WHITE)
            maxLines = 4
            setBackgroundColor(Color.rgb(18, 22, 30))
            setPadding(18, 12, 18, 12)
        }
        val send = Button(this).apply {
            text = "ENVIAR"
            setOnClickListener { sendMessage() }
        }
        val composer = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.BOTTOM
            addView(input, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
            addView(send, LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT))
        }
        root.addView(title, ViewGroup.LayoutParams(-1, ViewGroup.LayoutParams.WRAP_CONTENT))
        root.addView(status, ViewGroup.LayoutParams(-1, ViewGroup.LayoutParams.WRAP_CONTENT))
        root.addView(scroll, LinearLayout.LayoutParams(-1, 0, 1f))
        root.addView(composer, ViewGroup.LayoutParams(-1, ViewGroup.LayoutParams.WRAP_CONTENT))
        setContentView(root)
        checkConnectivity()
    }

    private fun checkConnectivity() {
        Deep33Api.submit {
            try {
                val health = Deep33Api.get("/health")
                val diag = Deep33Api.get("/v1/ai/diagnostics")
                val pass = health.optString("status") == "PASS" && diag.optBoolean("online", false)
                runOnUiThread {
                    status.text = if (pass) "ONLINE · BACKEND PASS · AI PASS"
                    else "OFFLINE · diagnostics FAIL"
                    status.setTextColor(if (pass) Color.rgb(0, 255, 140) else Color.rgb(255, 80, 80))
                }
            } catch (_: Exception) {
                runOnUiThread {
                    status.text = "OFFLINE · error de red"
                    status.setTextColor(Color.rgb(255, 80, 80))
                }
            }
        }
    }

    private fun sendMessage() {
        val text = input.text.toString().trim()
        if (text.isEmpty()) return
        input.setText("")
        appendBubble("TÚ", text, Color.rgb(30, 38, 50))

        val requestMessages = JSONArray().apply {
            for (i in 0 until messages.length()) put(messages.get(i))
            put(JSONObject().put("role", "user").put("content", text))
        }

        Deep33Api.submit {
            try {
                val response = Deep33Api.chat(requestMessages)
                val content = extractAssistantText(response)
                messages.put(JSONObject().put("role", "user").put("content", text))
                messages.put(JSONObject().put("role", "assistant").put("content", content))
                runOnUiThread { appendBubble("DEEP33", content, Color.rgb(12, 35, 40)) }
            } catch (e: Exception) {
                runOnUiThread { appendBubble("ERROR", e.message ?: "fallo de conectividad", Color.rgb(55, 20, 25)) }
            }
        }
    }

    private fun extractAssistantText(root: JSONObject): String {
        val response = root.optJSONObject("response") ?: error("Respuesta AI ausente")
        val choices = response.optJSONArray("choices") ?: error("choices ausente")
        val first = choices.optJSONObject(0) ?: error("choice ausente")
        return first.optJSONObject("message")?.optString("content")
            ?.takeIf { it.isNotBlank() } ?: error("contenido AI ausente")
    }

    private fun appendBubble(label: String, content: String, background: Int) {
        val bubble = TextView(this).apply {
            text = "$label\n$content"
            setTextColor(Color.WHITE)
            textSize = 16f
            setPadding(18, 14, 18, 14)
            setBackgroundColor(background)
        }
        val params = LinearLayout.LayoutParams(-1, ViewGroup.LayoutParams.WRAP_CONTENT)
        params.setMargins(0, 0, 0, 12)
        chatContainer.addView(bubble, params)
    }
}
