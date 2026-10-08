package cl.caggrometal.deep33

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.net.ConnectivityManager
import android.net.Network
import android.os.Build
import android.os.IBinder
import android.os.Handler
import android.os.Looper
import android.os.PowerManager
import org.json.JSONArray
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

class Deep33GenerationService : Service() {
    private val executor = Executors.newSingleThreadExecutor()
    private val userCancelled = AtomicBoolean(false)
    @Volatile private var stoppingBySystem = false
    @Volatile private var runningRequestId: String? = null
    @Volatile private var backgroundMode = false
    private var generationWakeLock: PowerManager.WakeLock? = null
    private var connectivityManager: ConnectivityManager? = null
    private var connectivityCallback: ConnectivityManager.NetworkCallback? = null
    private val networkRecoveryHandler = Handler(Looper.getMainLooper())
    private var scheduledNetworkRecovery: Runnable? = null

    override fun onCreate() {
        super.onCreate()
        createNotificationChannel()
        registerConnectivityMonitor()
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
                    Deep33Api.cancelActiveStream(requestId ?: runningRequestId)
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
            // Queue remote memory durably. Network synchronization is deliberately
            // outside the generation critical path so DONE can release the service wake lock.
            store.savePendingMemorySync(
                PendingMemorySync(
                    sessionId = pending.sessionId,
                    requestId = pending.requestId,
                    personality = personality.key,
                    memoryProfileId = store.memoryProfileId,
                    messagesJson = memoryPayload.toString()
                )
            )
            MemorySyncCoordinator.enqueue(this)
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
                // All transport recovery is handled by streamWithBackgroundRecovery().
                // When that bounded loop is exhausted, fail this generation exactly once
                // instead of launching a second recovery scheduler.
                // Preserve the original pending turn and idempotency key for an explicit
                // user retry. No second request is created automatically.
                store.saveGenerationState(
                    status = GenerationStatus.RETRYABLE,
                    requestId = requestId,
                    sessionId = pending.sessionId,
                    personality = personality.key,
                    partialOutput = "",
                    error = e.message ?: "La conexión con DEEP33 no pudo recuperarse.",
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
            releaseGenerationWakeLock()
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
        val checkpoint = StringBuilder()
        val generationDeadlineNanos =
            System.nanoTime() + GENERATION_DEADLINE_MS * 1_000_000L
        var lastCheckpointAt = 0L
        var lastCheckpointChars = 0

        fun persistCheckpoint(force: Boolean = false) {
            // Recovery checkpoints are throttled. Final delivery remains idempotent and
            // durable; streaming UI state is carried in-memory/event-driven.
            val now = android.os.SystemClock.elapsedRealtime()
            val enoughTime = now - lastCheckpointAt >= 750L
            val enoughText = checkpoint.length - lastCheckpointChars >= 1600
            if (!force && !enoughTime && !enoughText) return
            store.queueGenerationCheckpoint(
                status = GenerationStatus.RUNNING,
                requestId = requestId,
                sessionId = pending.sessionId,
                personality = personality.key,
                partialOutput = checkpoint.toString()
            )
            lastCheckpointAt = now
            lastCheckpointChars = checkpoint.length
        }

        for (attempt in 0..MAX_STREAM_RECOVERY_RETRIES) {
            if (userCancelled.get() || Thread.currentThread().isInterrupted) {
                throw Deep33ApiException(Deep33ApiException.Kind.CANCELLED)
            }

            val remainingBeforeAttemptMs =
                (generationDeadlineNanos - System.nanoTime()) / 1_000_000L
            if (remainingBeforeAttemptMs <= 250L) {
                throw Deep33ApiException(Deep33ApiException.Kind.TIMEOUT)
            }

            try {
                if (attempt == 0) {
                    android.util.Log.i(
                        "DEEP33_PERF",
                        "PERF T1_REQUEST_SENT request_id=$requestId"
                    )
                }
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
                    },
                    deadlineAtNanos = generationDeadlineNanos
                )
                persistCheckpoint(force = true)
                return result
            } catch (e: Deep33ApiException) {
                lastError = e

                // Transport recovery uses the single stream path below. Do not start a
                // parallel non-stream generation here: doing so could create a second
                // provider inference while the original request is still completing.

                if (!isRecoverableTransportError(e.kind) || attempt == MAX_STREAM_RECOVERY_RETRIES) {
                    throw e
                }

                // A retry starts from the durable request, not from a partial stream.
                // The backend receives the same idempotency key and can replay the exact
                // completed answer if the first transport died after inference completed.
                checkpoint.setLength(0)
                store.saveGenerationState(
                    status = GenerationStatus.RUNNING,
                    requestId = requestId,
                    sessionId = pending.sessionId,
                    personality = personality.key,
                    partialOutput = ""
                )
                updateForegroundNotification("Reconectando…")
                val remainingBeforeBackoffMs =
                    (generationDeadlineNanos - System.nanoTime()) / 1_000_000L
                if (remainingBeforeBackoffMs <= 250L) {
                    throw Deep33ApiException(Deep33ApiException.Kind.TIMEOUT)
                }
                val backoffMs = minOf(
                    RETRY_DELAYS_MS[attempt],
                    remainingBeforeBackoffMs - 100L
                ).coerceAtLeast(1L)
                try {
                    Thread.sleep(backoffMs)
                } catch (_: InterruptedException) {
                    Thread.currentThread().interrupt()
                    throw Deep33ApiException(Deep33ApiException.Kind.CANCELLED)
                }
            }
        }

        throw lastError ?: Deep33ApiException(Deep33ApiException.Kind.NETWORK)
    }

    private fun registerConnectivityMonitor() {
        val manager = getSystemService(ConnectivityManager::class.java) ?: return
        connectivityManager = manager
        if (connectivityCallback != null) return

        val callback = object : ConnectivityManager.NetworkCallback() {
            override fun onAvailable(network: Network) {
                // A Wi-Fi -> mobile handoff can emit onLost for the old route just
                // before the replacement route becomes active. Cancel the pending
                // transport abort as soon as Android reports a usable network.
                clearScheduledNetworkRecovery()
            }

            override fun onCapabilitiesChanged(
                network: Network,
                networkCapabilities: android.net.NetworkCapabilities
            ) {
                if (networkCapabilities.hasCapability(
                        android.net.NetworkCapabilities.NET_CAPABILITY_INTERNET
                    )
                ) {
                    clearScheduledNetworkRecovery()
                } else if (network == manager.activeNetwork) {
                    scheduleNetworkRecovery(manager)
                }
            }

            override fun onLost(network: Network) {
                val requestId = runningRequestId
                if (!requestId.isNullOrBlank()) {
                    // Do not tear down the stream immediately: Android may be handing
                    // off from Wi-Fi to mobile data. Give the replacement default route
                    // a short window to become active, while still bounding a true loss
                    // to hundreds of milliseconds instead of the full stream timeout.
                    scheduleNetworkRecovery(manager)
                }
            }
        }

        connectivityCallback = callback
        runCatching {
            manager.registerDefaultNetworkCallback(callback)
        }.onFailure {
            connectivityCallback = null
        }
    }

    private fun scheduleNetworkRecovery(manager: ConnectivityManager) {
        if (runningRequestId.isNullOrBlank()) return
        clearScheduledNetworkRecovery()

        val requestId = runningRequestId
        val recovery = Runnable {
            scheduledNetworkRecovery = null
            if (requestId != runningRequestId || requestId.isNullOrBlank()) return@Runnable

            val activeNetwork = manager.activeNetwork
            val capabilities = activeNetwork?.let { manager.getNetworkCapabilities(it) }
            val hasInternet = capabilities?.hasCapability(
                android.net.NetworkCapabilities.NET_CAPABILITY_INTERNET
            ) == true

            if (!hasInternet) {
                // Force a fast transport failure so the durable recovery loop can
                // switch routes/retry immediately instead of waiting for readTimeout.
                Deep33Api.cancelActiveStream(requestId)
                updateForegroundNotification("Red perdida · reconectando…")
            }
        }

        scheduledNetworkRecovery = recovery
        networkRecoveryHandler.postDelayed(recovery, NETWORK_RECOVERY_GRACE_MS)
    }

    private fun clearScheduledNetworkRecovery() {
        scheduledNetworkRecovery?.let { networkRecoveryHandler.removeCallbacks(it) }
        scheduledNetworkRecovery = null
    }

    private fun unregisterConnectivityMonitor() {
        clearScheduledNetworkRecovery()
        connectivityCallback?.let { callback ->
            runCatching { connectivityManager?.unregisterNetworkCallback(callback) }
        }
        connectivityCallback = null
        connectivityManager = null
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
        // Mark this as a system-initiated shutdown before touching durable state so
        // onDestroy/runGeneration cannot turn a timeout recovery marker into a
        // terminal failure (FAILED -> RUNNING race).
        stoppingBySystem = true
        val requestId = runningRequestId
        if (!requestId.isNullOrBlank()) {
            val store = SessionStore(this)
            val pending = store.loadPendingTurn()
            val state = store.loadGenerationState()
            if (
                pending?.requestId == requestId &&
                state?.requestId == requestId &&
                state.status != GenerationStatus.DONE &&
                state.status != GenerationStatus.CANCELLED
            ) {
                store.saveGenerationState(
                    status = GenerationStatus.RUNNING,
                    requestId = requestId,
                    sessionId = pending.sessionId,
                    personality = pending.personality,
                    partialOutput = state.partialOutput,
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
        if (state?.requestId != pending.requestId) return

        // Recovery checkpoints may only preserve a non-terminal generation. A service
        // teardown must never overwrite DONE/FAILED/CANCELLED with RUNNING.
        if (
            state.status == GenerationStatus.DONE ||
            state.status == GenerationStatus.FAILED ||
            state.status == GenerationStatus.CANCELLED
        ) {
            return
        }

        store.saveGenerationState(
            status = GenerationStatus.RUNNING,
            requestId = pending.requestId,
            sessionId = pending.sessionId,
            personality = Personality.fromKey(pending.personality).key,
            partialOutput = state.partialOutput,
            error = state.error,
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
                SessionStore(this).flushGenerationCheckpoint(it.requestId)
                persistRecoveryCheckpoint(it)
            }
        }
        stoppingBySystem = true
        Deep33Api.cancelActiveStream(runningRequestId)
        unregisterConnectivityMonitor()
        releaseGenerationWakeLock()
        executor.shutdownNow()
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    companion object {
        private const val CHANNEL_ID = "deep33_generation"
        private const val NOTIFICATION_ID = 3301
        private const val MAX_STREAM_RECOVERY_RETRIES = 5
        private const val GENERATION_DEADLINE_MS = 180_000L
        private const val NETWORK_RECOVERY_GRACE_MS = 350L
        private val RETRY_DELAYS_MS = longArrayOf(1_000L, 2_000L, 4_000L, 8_000L, 16_000L)
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
