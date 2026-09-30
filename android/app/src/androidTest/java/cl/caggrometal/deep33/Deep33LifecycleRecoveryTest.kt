package cl.caggrometal.deep33

import android.content.ComponentName
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.widget.TextView
import androidx.lifecycle.Lifecycle
import androidx.test.core.app.ActivityScenario
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class Deep33LifecycleRecoveryTest {
    @Test
    fun generationServiceContractIsForegroundAndTaskIndependent() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val info = context.packageManager.getServiceInfo(
            ComponentName(context, Deep33GenerationService::class.java),
            PackageManager.GET_META_DATA
        )

        assertTrue(
            "Generation service must declare dataSync foreground execution",
            info.foregroundServiceType and ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC != 0
        )
        assertTrue(
            "Generation service must not stop with the Activity task",
            info.flags and ServiceInfo.FLAG_STOP_WITH_TASK == 0
        )
        assertTrue(
            "Generation service must remain in the application process",
            info.flags and ServiceInfo.FLAG_ISOLATED_PROCESS == 0
        )
    }

    @Test
    fun pendingTurnSurvivesBackgroundForegroundAndGetsResponse() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val store = SessionStore(context)
        store.resetSession()
        val pending = PendingTurn(
            sessionId = store.sessionId,
            requestId = "lifecycle-e2e-request",
            idempotencyKey = "chat-lifecycle-e2e-request",
            personality = "NEUTRO",
            payloadJson = """[{"role":"user","content":"Responde exactamente: OK"}]"""
        )
        store.savePendingTurn(pending)

        val scenario = ActivityScenario.launch(MainActivity::class.java)
        try {
            scenario.onActivity { activity ->
                val status = activity.readPrivateTextView("statusView")
                assertTrue("Initial state lost the pending turn", !status.contains("OFFLINE"))
            }

            // Simulate the user leaving the app for several seconds while the turn is pending.
            scenario.moveToState(Lifecycle.State.CREATED)
            Thread.sleep(7_000L)
            scenario.moveToState(Lifecycle.State.RESUMED)

            val deadline = System.currentTimeMillis() + 60_000L
            var responseReceived = false
            var offlineSeen = false
            while (System.currentTimeMillis() < deadline) {
                InstrumentationRegistry.getInstrumentation().waitForIdleSync()
                scenario.onActivity { activity ->
                    val status = activity.readPrivateTextView("statusView")
                    if (status.contains("OFFLINE")) offlineSeen = true

                    @Suppress("UNCHECKED_CAST")
                    val messages = activity.readPrivateConversation() as List<UiMessage>
                    responseReceived = messages.any { it.role == "assistant" && it.content.isNotBlank() }
                }
                if (responseReceived && SessionStore(context).loadPendingTurn() == null) break
                Thread.sleep(250L)
            }

            assertTrue("Lifecycle recovery exposed OFFLINE while recovering the pending turn", !offlineSeen)
            assertTrue(
                "The original pending turn did not produce an assistant response",
                responseReceived
            )
            assertNull(
                "Pending marker was not cleared after the recovered response",
                SessionStore(context).loadPendingTurn()
            )
        } finally {
            store.clearPendingTurn(pending.requestId)
            scenario.close()
        }
    }

    @Test
    fun pendingTurnSurvivesActivityProcessState() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val store = SessionStore(context)
        store.resetSession()
        val pending = PendingTurn(
            sessionId = store.sessionId,
            requestId = "lifecycle-test-request",
            idempotencyKey = "chat-lifecycle-test-request",
            personality = "NEUTRO",
            payloadJson = """[{"role":"user","content":"lifecycle test"}]"""
        )

        store.savePendingTurn(pending)
        val recreatedStore = SessionStore(context)
        val restored = recreatedStore.loadPendingTurn()

        assertTrue(restored?.sessionId == pending.sessionId)
        assertTrue(restored?.requestId == pending.requestId)
        assertTrue(restored?.idempotencyKey == pending.idempotencyKey)
        assertTrue(restored?.payloadJson == pending.payloadJson)

        recreatedStore.clearPendingTurn(pending.requestId)
        assertNull(recreatedStore.loadPendingTurn())
    }
    private fun Any.readPrivateTextView(fieldName: String): String {
        val field = javaClass.getDeclaredField(fieldName).apply { isAccessible = true }
        return (field.get(this) as TextView).text?.toString().orEmpty()
    }

    private fun Any.readPrivateConversation(): Any {
        val field = javaClass.getDeclaredField("conversation").apply { isAccessible = true }
        return field.get(this)
    }


}
