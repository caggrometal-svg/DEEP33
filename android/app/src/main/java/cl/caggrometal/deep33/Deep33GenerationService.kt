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
import android.os.PowerManager
import org.json.JSONArray
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

class Deep33GenerationService : Service() {
    private val executor = Executors.newSingleThreadExecutor()
    private val userCancelled = AtomicBoolean(false)
    @Volatile private var stoppingBySystem = false
    @Volatile private var runningRequestId: String? = null
    @Volatile private var keepAliveForRecovery = false
    @Volatile private var backgroundMode = false
    private var backgroundRecoveryCycles = 0
    private var generationWakeLock: PowerManager.WakeLock? = null

    override fun onCreate() {
        super.onCreate()
        createNotificationChannel()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_BACKGROUND -> {
                val requestId = intent.getStringExtra(EXTRA_REQUEST_ID)
                if (requestId.isNullOrBlank() || requestId == runningRequestId) {
                    SessionStore(this).appBackgrounded = true
                    backgroundMode = true
                    // Backgrounding the Activity must not terminate the transport. The
                    // foreground service owns the generation and keeps the active stream
                    // alive while the UI is temporarily invisible.
                    updateForegroundNotification("DEEP33 continúa generando en segundo plano…")
                }
                return START_REDELIVER_INTENT
            }

            ACTION_FOREGROUND -> {
                val requestId = intent.getStringExtra(EXTRA_REQUEST_ID)
                if (requestId.isNullOrBlank() || requestId == runningRequestId) {
                    SessionStore(this).appBackgrounded = false
                    backgroundMode = false
                    updateForegroundNotification("Generando respuesta…")
                }
                return START_REDELIVER_INTENT
            }

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
                keepAliveForRecovery = false
                // Recover lifecycle state after Android recreates the service process.
                backgroundMode = SessionStore(this).appBackgrounded
                runningRequestId = requestedId
                acquireGenerationWakeLock()
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

            val memoryPayload = JSONArray()
            messages.takeLast(50).forEach {
                memoryPayload.put(
                    org.json.JSONObject()
                        .put("role", it.role)
                        .put("content", it.content)
                )
            }
            // Queue remote memory durably before attempting the network write. The local
            // conversation is already safe; this marker guarantees that memory sync can
            // be retried after process death, network loss, or a temporary backend outage.
            store.savePendingMemorySync(
                PendingMemorySync(
                    sessionId = pending.sessionId,
                    requestId = pending.requestId,
                    personality = personality.key,
                    memoryProfileId = store.memoryProfileId,
                    messagesJson = memoryPayload.toString()
                )
            )
            try {
                Deep33Api.syncMemory(
                    pending.sessionId,
                    memoryPayload,
                    personality.key,
                    requestId = pending.requestId,
                    memoryProfileId = store.memoryProfileId
                )
                store.clearPendingMemorySync(pending.requestId)
            } catch (_: Exception) {
                // The durable memory-sync marker remains for MainActivity startup recovery.
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
                    store.saveGenerationState(
                        status = GenerationStatus.FAILED,
                        requestId = requestId,
                        sessionId = pending.sessionId,
                        personality = personality.key,
                        error = e.message ?: "Error de comunicación con DEEP33.",
                        durable = true
                    )
                } else {
                    // Do not terminate a background generation merely because the stream
                    // exhausted its short transport retry budget. Keep the foreground
                    // service alive and retry from the same durable request/idempotency key.
                    store.saveGenerationState(
                        status = GenerationStatus.RUNNING,
                        requestId = requestId,
                        sessionId = pending.sessionId,
                        personality = personality.key,
                        partialOutput = "",
                        error = "Conexión interrumpida. DEEP33 reintentará en segundo plano.",
                        durable = true
                    )
                    updateForegroundNotification("Reconectando en segundo plano…")
                    if (backgroundRecoveryCycles < MAX_BACKGROUND_RECOVERY_CYCLES) {
                        keepAliveForRecovery = true
                        scheduleBackgroundRecovery(requestId)
                    } else {
                        store.saveGenerationState(
                            status = GenerationStatus.FAILED,
                            requestId = requestId,
                            sessionId = pending.sessionId,
                            personality = personality.key,
                            partialOutput = "",
                            error = "Conexión interrumpida. La solicitud quedó guardada para recuperación.",
                            durable = true
                        )
                    }
                }
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
            if (!keepAliveForRecovery) {
                runningRequestId = null
                backgroundRecoveryCycles = 0
                releaseGenerationWakeLock()
                stopSelf()
            }
        }
    }

    private fun scheduleBackgroundRecovery(requestId: String) {
        val delay = BACKGROUND_RECOVERY_DELAYS_MS[
            backgroundRecoveryCycles.coerceAtMost(BACKGROUND_RECOVERY_DELAYS_MS.lastIndex)
        ]
        backgroundRecoveryCycles++
        executor.execute {
            try {
                Thread.sleep(delay)
            } catch (_: InterruptedException) {
                Thread.currentThread().interrupt()
                return@execute
            }

            if (userCancelled.get() || stoppingBySystem) return@execute

            val pending = SessionStore(this).loadPendingTurn()
            if (pending?.requestId != requestId) return@execute

            keepAliveForRecovery = false
            updateForegroundNotification("Reconectando en segundo plano…")
            runGeneration(requestId)
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
        val checkpoint = StringBuilder()
        var lastPersistedLength = 0
        var lastCheckpointAt = System.nanoTime()

        fun persistCheckpoint(force: Boolean = false) {
            val now = System.nanoTime()
            // Durable recovery does not need a disk write for every small token burst.
            // Checkpoint less often while keeping sub-second recovery visibility.
            val dueByBytes = checkpoint.length - lastPersistedLength >= 1_024
            val dueByTime = now - lastCheckpointAt >= 300_000_000L
            if (!force && !dueByBytes && !dueByTime) return

            store.saveGenerationState(
                status = GenerationStatus.RUNNING,
                requestId = requestId,
                sessionId = pending.sessionId,
                personality = personality.key,
                partialOutput = checkpoint.toString()
            )
            lastPersistedLength = checkpoint.length
            lastCheckpointAt = now
        }

        for (attempt in 0..MAX_STREAM_RECOVERY_RETRIES) {
            if (userCancelled.get() || Thread.currentThread().isInterrupted) {
                throw Deep33ApiException(Deep33ApiException.Kind.CANCELLED)
            }

            try {
                val result = Deep33Api.stream(
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
                        checkpoint.append(chunk)
                        persistCheckpoint()
                    }
                )
                persistCheckpoint(force = true)
                return result
            } catch (e: Deep33ApiException) {
                lastError = e

                // When the Activity goes to the background, do not depend on a long-lived
                // SSE socket. The same request/idempotency key is completed through the
                // non-streaming endpoint, whose result is durable and can be recovered
                // when the Activity returns.
                if (backgroundMode && isRecoverableTransportError(e.kind)) {
                    try {
                        val direct = Deep33Api.generate(
                            payload,
                            pending.sessionId,
                            personality.key,
                            requestId = pending.requestId,
                            idempotencyKey = pending.idempotencyKey,
                        )
                        val directText = direct
                            .optJSONObject("result")
                            ?.optString("text")
                            .orEmpty()
                            .trim()
                        if (directText.isNotBlank()) {
                            store.saveGenerationState(
                                status = GenerationStatus.RUNNING,
                                requestId = requestId,
                                sessionId = pending.sessionId,
                                personality = personality.key,
                                partialOutput = directText,
                                error = "Continuando en segundo plano."
                            )
                            return directText
                        }
                    } catch (fallbackError: Exception) {
                        lastError = when (fallbackError) {
                            is Deep33ApiException -> fallbackError
                            else -> Deep33ApiException(
                                Deep33ApiException.Kind.NETWORK,
                                cause = fallbackError
                            )
                        }
                    }
                }

                if (!isRecoverableTransportError(e.kind) || attempt == MAX_STREAM_RECOVERY_RETRIES) {
                    throw e
                }

                // A retry starts from the durable request, not from a partial stream.
                // The backend receives the same idempotency key and can replay the exact
                // completed answer if the first transport died after inference completed.
                checkpoint.setLength(0)
                lastPersistedLength = 0
                lastCheckpointAt = System.nanoTime()
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

    private fun acquireGenerationWakeLock() {
        if (generationWakeLock?.isHeld == true) return
        val manager = getSystemService(Context.POWER_SERVICE) as PowerManager
        generationWakeLock = manager.newWakeLock(
            PowerManager.PARTIAL_WAKE_LOCK,
            "DEEP33:Generation"
        ).apply {
            setReferenceCounted(false)
            acquire()
        }
    }

    private fun releaseGenerationWakeLock() {
        runCatching {
            generationWakeLock?.takeIf { it.isHeld }?.release()
        }
        generationWakeLock = null
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
        if (!userCancelled.get()) {
            pending?.takeIf { it.requestId == runningRequestId }?.let {
                persistRecoveryCheckpoint(it)
            }
        }
        stoppingBySystem = true
        Deep33Api.cancelActiveStream()
        releaseGenerationWakeLock()
        executor.shutdownNow()
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    companion object {
        private const val CHANNEL_ID = "deep33_generation"
        private const val NOTIFICATION_ID = 3301
        private const val MAX_STREAM_RECOVERY_RETRIES = 5
        private val RETRY_DELAYS_MS = longArrayOf(1_000L, 2_000L, 4_000L, 8_000L, 16_000L)
        private const val MAX_BACKGROUND_RECOVERY_CYCLES = 3
        private val BACKGROUND_RECOVERY_DELAYS_MS = longArrayOf(15_000L, 30_000L, 60_000L)
        private const val ACTION_START = "cl.caggrometal.deep33.action.START_GENERATION"
        private const val ACTION_CANCEL = "cl.caggrometal.deep33.action.CANCEL_GENERATION"
        private const val ACTION_BACKGROUND = "cl.caggrometal.deep33.action.APP_BACKGROUND"
        private const val ACTION_FOREGROUND = "cl.caggrometal.deep33.action.APP_FOREGROUND"
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

        fun appBackground(context: Context, requestId: String?) {
            val intent = Intent(context, Deep33GenerationService::class.java)
                .setAction(ACTION_BACKGROUND)
                .putExtra(EXTRA_REQUEST_ID, requestId)
            context.startService(intent)
        }

        fun appForeground(context: Context, requestId: String?) {
            val intent = Intent(context, Deep33GenerationService::class.java)
                .setAction(ACTION_FOREGROUND)
                .putExtra(EXTRA_REQUEST_ID, requestId)
            context.startService(intent)
        }
    }
}
