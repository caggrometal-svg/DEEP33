package cl.caggrometal.deep33

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
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

                startForegroundCompat("Generando respuesta…")

                userCancelled.set(false)
                stoppingBySystem = false
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
        updateForegroundNotification("Generando respuesta…")

        try {
            val finalText = streamWithBackgroundRecovery(
                store = store,
                pending = pending,
                personality = personality,
                requestId = requestId,
                payload = payload
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
            // Establish the recovery boundary before touching any non-critical remote
            // services. If the process dies immediately after this point, the exact
            // conversation and DONE marker are already durable and the pending marker
            // can be safely removed.
            store.saveMessages(messages, durable = true)

            val title = messages.firstOrNull { it.role == "user" }
                ?.content
                ?.replace(Regex("\\s+"), " ")
                ?.trim()
                ?.take(60)
            if (!title.isNullOrBlank()) {
                store.saveChatSummary(title)
            }

            store.saveGenerationState(
                status = GenerationStatus.DONE,
                requestId = requestId,
                sessionId = pending.sessionId,
                personality = personality.key,
                partialOutput = finalText,
                finalText = finalText,
                durable = true
            )
            store.clearPendingTurn(requestId)

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
                // The local recovery boundary is already complete; remote memory is best effort.
            }
        } catch (e: Deep33ApiException) {
            if (userCancelled.get()) {
                store.clearPendingTurn(requestId)
                store.saveGenerationState(
                    status = GenerationStatus.CANCELLED,
                    requestId = requestId,
                    sessionId = pending.sessionId,
                    personality = personality.key,
                    error = "Generación cancelada.",
                    durable = true
                )
            } else if (!stoppingBySystem) {
                // Keep transient transport failures recoverable across Activity/process
                // recreation. The same idempotency key lets the backend replay a completed
                // answer instead of creating a second generation.
                val recoverable = isRecoverableTransportError(e.kind)
                if (!recoverable) {
                    store.clearPendingTurn(requestId)
                }
                store.saveGenerationState(
                    status = GenerationStatus.FAILED,
                    requestId = requestId,
                    sessionId = pending.sessionId,
                    personality = personality.key,
                    error = if (recoverable) {
                        "Conexión interrumpida. La solicitud quedó guardada para recuperación."
                    } else {
                        e.message ?: "Error de comunicación con DEEP33."
                    },
                    durable = true
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
                    error = e.message ?: "Error inesperado de comunicación.",
                    durable = true
                )
            }
        } finally {
            runningRequestId = null
            stopSelf()
        }
    }

    private fun streamWithBackgroundRecovery(
        store: SessionStore,
        pending: PendingTurn,
        personality: Personality,
        requestId: String,
        payload: JSONArray
    ): String {
        var lastError: Deep33ApiException? = null

        for (attempt in 0..MAX_STREAM_RECOVERY_RETRIES) {
            if (userCancelled.get() || Thread.currentThread().isInterrupted) {
                throw Deep33ApiException(Deep33ApiException.Kind.CANCELLED)
            }

            try {
                return Deep33Api.stream(
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
            } catch (e: Deep33ApiException) {
                lastError = e
                if (!isRecoverableTransportError(e.kind) || attempt == MAX_STREAM_RECOVERY_RETRIES) {
                    throw e
                }

                // A retry starts from the durable request, not from a partial stream.
                // The backend receives the same idempotency key and can replay the exact
                // completed answer if the first transport died after inference completed.
                store.saveGenerationState(
                    status = GenerationStatus.RUNNING,
                    requestId = requestId,
                    sessionId = pending.sessionId,
                    personality = personality.key,
                    partialOutput = ""
                )
                updateForegroundNotification("Reconectando…")
                try {
                    Thread.sleep(RETRY_DELAYS_MS[attempt])
                } catch (_: InterruptedException) {
                    Thread.currentThread().interrupt()
                    throw Deep33ApiException(Deep33ApiException.Kind.CANCELLED)
                }
            }
        }

        throw lastError ?: Deep33ApiException(Deep33ApiException.Kind.NETWORK)
    }

    private fun isRecoverableTransportError(kind: Deep33ApiException.Kind): Boolean =
        kind == Deep33ApiException.Kind.NETWORK ||
            kind == Deep33ApiException.Kind.TIMEOUT ||
            kind == Deep33ApiException.Kind.SERVER

    private fun startForegroundCompat(text: String) {
        val notification = buildNotification(text)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(
                NOTIFICATION_ID,
                notification,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC
            )
        } else {
            startForeground(NOTIFICATION_ID, notification)
        }
    }

    private fun updateForegroundNotification(text: String) {
        // Re-submit the existing foreground notification instead of calling
        // NotificationManager.notify(), which would require POST_NOTIFICATIONS on
        // Android 13+ and is not necessary for an active foreground service.
        startForegroundCompat(text)
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
                // Never discard the durable request when Android ends a data-sync
                // foreground service because of its platform time budget.
                store.saveGenerationState(
                    status = GenerationStatus.FAILED,
                    requestId = requestId,
                    sessionId = pending.sessionId,
                    personality = pending.personality,
                    error = "La generación superó el límite temporal de segundo plano. La solicitud quedó guardada para continuar.",
                    durable = true
                )
            }
        }
        stopSelf()
    }

    override fun onTaskRemoved(rootIntent: Intent?) {
        // stopWithTask=false keeps this service alive even when the user dismisses
        // the DEEP33 task from the recent-apps list.
        if (runningRequestId != null) {
            updateForegroundNotification("DEEP33 continúa generando en segundo plano…")
        }
        super.onTaskRemoved(rootIntent)
    }

    private fun persistRecoveryCheckpoint(pending: PendingTurn) {
        val store = SessionStore(this)
        val state = store.loadGenerationState()
        store.saveGenerationState(
            status = GenerationStatus.RUNNING,
            requestId = pending.requestId,
            sessionId = pending.sessionId,
            personality = Personality.fromKey(pending.personality).key,
            partialOutput = state?.takeIf { it.requestId == pending.requestId }?.partialOutput.orEmpty(),
            durable = true
        )
    }

    override fun onDestroy() {
        // Android may recreate a foreground service after process reclamation. Preserve
        // a durable RUNNING checkpoint before closing the socket so the redelivered
        // request can continue from the same persisted turn instead of becoming lost.
        val pending = SessionStore(this).loadPendingTurn()
        if (pending?.requestId == runningRequestId && !userCancelled.get()) {
            persistRecoveryCheckpoint(pending)
        }
        stoppingBySystem = true
        Deep33Api.cancelActiveStream()
        executor.shutdownNow()
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    companion object {
        private const val CHANNEL_ID = "deep33_generation"
        private const val NOTIFICATION_ID = 3301
        private const val MAX_STREAM_RECOVERY_RETRIES = 5
        private val RETRY_DELAYS_MS = longArrayOf(1_000L, 2_000L, 4_000L, 8_000L, 16_000L)
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
