package cl.caggrometal.deep33

import android.content.Context
import android.util.Log
import org.json.JSONArray
import java.util.concurrent.Executors
import java.util.concurrent.ScheduledExecutorService
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Durable remote-memory queue processor. It is intentionally independent of the
 * foreground generation service and never owns a generation wake lock.
 */
object MemorySyncCoordinator {
    private const val RETRY_DELAY_MS = 5_000L

    private val executor = Executors.newSingleThreadExecutor { runnable ->
        Thread(runnable, "DEEP33-MemorySync").apply { isDaemon = true }
    }
    private val scheduler: ScheduledExecutorService =
        Executors.newSingleThreadScheduledExecutor { runnable ->
            Thread(runnable, "DEEP33-MemorySyncScheduler").apply { isDaemon = true }
        }
    private val queued = AtomicBoolean(false)

    fun enqueue(context: Context, delayMs: Long = 0L) {
        val appContext = context.applicationContext
        val submit = Runnable {
            if (!queued.compareAndSet(false, true)) return@Runnable
            executor.execute { process(appContext) }
        }
        if (delayMs <= 0L) submit.run()
        else scheduler.schedule(submit, delayMs, TimeUnit.MILLISECONDS)
    }

    private fun process(context: Context) {
        try {
            val store = SessionStore(context)
            val pending = store.loadPendingMemorySync()
            if (pending == null) {
                queued.set(false)
                return
            }

            val payload = JSONArray(pending.messagesJson)
            Deep33Api.syncMemory(
                pending.sessionId,
                payload,
                pending.personality,
                requestId = pending.requestId,
                memoryProfileId = pending.memoryProfileId
            )
            store.clearPendingMemorySync(pending.requestId)
            queued.set(false)

            if (store.loadPendingMemorySync() != null) enqueue(context)
        } catch (e: Exception) {
            queued.set(false)
            Log.w("DEEP33", "Memory sync deferred: " + e.javaClass.simpleName)
            enqueue(context, RETRY_DELAY_MS)
        }
    }
}
