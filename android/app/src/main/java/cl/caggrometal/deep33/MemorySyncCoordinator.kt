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
 *
 * Every queued profile gets one attempt per processing cycle. A failed profile
 * cannot starve a different profile's pending memory.
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
        val profiles = MultiUserIdentity.listProfiles(context)
        var hadPending = false
        var hadFailure = false

        for (profile in profiles) {
            val store = SessionStore(context, profileIdOverride = profile.id)
            val pending = store.loadPendingMemorySync() ?: continue
            hadPending = true

            try {
                val payload = JSONArray(pending.messagesJson)
                Deep33Api.configureAuth(context)
                Deep33Api.syncMemory(
                    pending.sessionId,
                    payload,
                    pending.personality,
                    requestId = pending.requestId,
                    memoryProfileId = pending.memoryProfileId
                )
                store.clearPendingMemorySync(pending.requestId)
            } catch (e: Exception) {
                hadFailure = true
                Log.w(
                    "DEEP33",
                    "Memory sync deferred profile=${profile.id}: ${e.javaClass.simpleName}"
                )
            }
        }

        queued.set(false)

        if (!hadPending) return

        val remaining = MultiUserIdentity.listProfiles(context).any {
            SessionStore(context, profileIdOverride = it.id).loadPendingMemorySync() != null
        }
        if (remaining) {
            enqueue(context, if (hadFailure) RETRY_DELAY_MS else 0L)
        }
    }
}
