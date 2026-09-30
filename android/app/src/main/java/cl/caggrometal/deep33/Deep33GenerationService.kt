package cl.caggrometal.deep33

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.IBinder
import org.json.JSONArray
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

class Deep33GenerationService : Service() {
    private val executor = Executors.newSingleThreadExecutor()
    private val userCancelled = AtomicBoolean(false)
    @Volatile private var stoppingBySystem = false
    @Volatile private var runningRequestId: String? = null

    override fun onCreate() {
        super.onCreate()
        createNotificationChannel()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_CANCEL -> {
                val requestId = intent.getStringExtra(EXTRA_REQUEST_ID)
                if (requestId.isNullOrBlank() || requestId == runningRequestId) {
                    userCancelled.set(true)
                    Deep33Api.cancelActiveStream()
                }
                return START_REDELIVER_INTENT
            }

            ACTION_START -> {
                val requestedId = intent.getStringExtra(EXTRA_REQUEST_ID).orEmpty()
                if (requestedId.isBlank()) {
                    stopSelfResult(startId)
                    return START_REDELIVER_INTENT
                }

                if (runningRequestId != null) {
                    if (runningRequestId == requestedId) return START_REDELIVER_INTENT
                    stopSelfResult(startId)
                    return START_REDELIVER_INTENT
                }

                startForeground(
                    NOTIFICATION_ID,
                    buildNotification("Generando respuesta…")
                )

                runningRequestId = requestedId
                executor.execute { runGeneration(requestedId) }
                return START_REDELIVER_INTENT
            }
        }

        stopSelfResult(startId)
        return START_REDELIVER_INTENT
    }

    private fun runGeneration(requestId: String) {
        val store = SessionStore(this)
        val pending = store.loadPendingTurn()
        if (pending == null || pending.requestId != requestId) {
            runningRequestId = null
            stopSelf()
            return
        }

        val payload = try {
            JSONArray(pending.payloadJson)
        } catch (_: Exception) {
            store.clearPendingTurn(requestId)
            store.saveGenerationState(
                GenerationStatus.FAILED,
                requestId,
                pending.sessionId,
                pending.personality,
                error = "La solicitud pendiente está corrupta."
            )
            runningRequestId = null
            stopSelf()
            return
        }

        val personality = Personality.fromKey(pending.personality)
        store.saveGenerationState(
            status = GenerationStatus.RUNNING,
            requestId = requestId,
            sessionId = pending.sessionId,
            personality = personality.key,
            partialOutput = ""
        )

        try {
            val finalText = Deep33Api.stream(
                payload,
                pending.sessionId,
                personality.key,
                requestId = pending.requestId,
                idempotencyKey = pending.idempotencyKey,
                memoryProfileId = store.memoryProfileId,
                isCancelled = {
                    userCancelled.get() || Thread.currentThread().isInterrupted
                },
                onText = { chunk ->
                    val current = store.loadGenerationState()?.partialOutput.orEmpty()
                    store.saveGenerationState(
                        status = GenerationStatus.RUNNING,
                        requestId = requestId,
                        sessionId = pending.sessionId,
                        personality = personality.key,
                        partialOutput = current + chunk
                    )
                }
            )

            if (finalText.isBlank()) {
                throw Deep33ApiException(Deep33ApiException.Kind.BAD_RESPONSE)
            }

            val messages = buildList {
                for (i in 0 until payload.length()) {
                    val item = payload.optJSONObject(i) ?: continue
                    val role = item.optString("role")
                    val content = item.optString("content")
                    if (role in setOf("user", "assistant") && content.isNotBlank()) {
                        add(UiMessage(role, content))
                    }
                }
                add(UiMessage("assistant", finalText))
            }
            store.activateSession(pending.sessionId)
            store.saveMessages(messages)

            val title = messages.firstOrNull { it.role == "user" }
                ?.content
                ?.replace(Regex("\\s+"), " ")
                ?.trim()
                ?.take(60)
            if (!title.isNullOrBlank()) {
                store.saveChatSummary(title)
            }

            try {
                val memoryPayload = JSONArray()
                messages.takeLast(50).forEach {
                    memoryPayload.put(
                        org.json.JSONObject()
                            .put("role", it.role)
                            .put("content", it.content)
                    )
                }
                Deep33Api.syncMemory(
                    pending.sessionId,
                    memoryPayload,
                    personality.key,
                    requestId = pending.requestId,
                    memoryProfileId = store.memoryProfileId
                )
            } catch (_: Exception) {
                // Local response is already durable; remote memory retries on the next turn.
            }

            store.clearPendingTurn(requestId)
            store.saveGenerationState(
                status = GenerationStatus.DONE,
                requestId = requestId,
                sessionId = pending.sessionId,
                personality = personality.key,
                partialOutput = finalText,
                finalText = finalText
            )
        } catch (e: Deep33ApiException) {
            if (userCancelled.get()) {
                store.clearPendingTurn(requestId)
                store.saveGenerationState(
                    status = GenerationStatus.CANCELLED,
                    requestId = requestId,
                    sessionId = pending.sessionId,
                    personality = personality.key,
                    error = "Generación cancelada."
                )
            } else if (!stoppingBySystem) {
                store.clearPendingTurn(requestId)
                store.saveGenerationState(
                    status = GenerationStatus.FAILED,
                    requestId = requestId,
                    sessionId = pending.sessionId,
                    personality = personality.key,
                    error = e.message ?: "Error de comunicación con DEEP33."
                )
            }
        } catch (e: Exception) {
            if (!stoppingBySystem) {
                store.clearPendingTurn(requestId)
                store.saveGenerationState(
                    status = GenerationStatus.FAILED,
                    requestId = requestId,
                    sessionId = pending.sessionId,
                    personality = personality.key,
                    error = e.message ?: "Error inesperado de comunicación."
                )
            }
        } finally {
            runningRequestId = null
            stopSelf()
        }
    }

    private fun buildNotification(text: String): Notification =
        Notification.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_deep33_flame)
            .setContentTitle("DEEP33")
            .setContentText(text)
            .setOngoing(true)
            .setCategory(Notification.CATEGORY_SERVICE)
            .setOnlyAlertOnce(true)
            .build()

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val manager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
            manager.createNotificationChannel(
                NotificationChannel(
                    CHANNEL_ID,
                    "DEEP33 generación",
                    NotificationManager.IMPORTANCE_LOW
                )
            )
        }
    }

    override fun onTimeout(startId: Int, fgsType: Int) {
        val requestId = runningRequestId
        if (!requestId.isNullOrBlank()) {
            val store = SessionStore(this)
            val pending = store.loadPendingTurn()
            if (pending?.requestId == requestId) {
                store.clearPendingTurn(requestId)
                store.saveGenerationState(
                    status = GenerationStatus.FAILED,
                    requestId = requestId,
                    sessionId = pending.sessionId,
                    personality = pending.personality,
                    error = "La generación superó el límite permitido en segundo plano."
                )
            }
        }
        stopSelf()
    }

    override fun onDestroy() {
        stoppingBySystem = true
        Deep33Api.cancelActiveStream()
        executor.shutdownNow()
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    companion object {
        private const val CHANNEL_ID = "deep33_generation"
        private const val NOTIFICATION_ID = 3301
        private const val ACTION_START = "cl.caggrometal.deep33.action.START_GENERATION"
        private const val ACTION_CANCEL = "cl.caggrometal.deep33.action.CANCEL_GENERATION"
        private const val EXTRA_REQUEST_ID = "request_id"

        fun start(context: Context, requestId: String) {
            val intent = Intent(context, Deep33GenerationService::class.java)
                .setAction(ACTION_START)
                .putExtra(EXTRA_REQUEST_ID, requestId)
            context.startForegroundService(intent)
        }

        fun cancel(context: Context, requestId: String?) {
            val intent = Intent(context, Deep33GenerationService::class.java)
                .setAction(ACTION_CANCEL)
                .putExtra(EXTRA_REQUEST_ID, requestId)
            context.startService(intent)
        }
    }
}
