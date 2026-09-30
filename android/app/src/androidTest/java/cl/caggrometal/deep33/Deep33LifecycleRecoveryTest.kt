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
    fun conversationSurvivesBackgroundForegroundWithoutNetwork() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val store = SessionStore(context)
        store.resetSession()

        val expected = listOf(
            UiMessage("user", "conversación de continuidad"),
            UiMessage("assistant", "respuesta persistida")
        )
        store.saveMessages(expected, durable = true)

        val scenario = ActivityScenario.launch(MainActivity::class.java)
        try {
            scenario.onActivity { activity ->
                val restored = activity.readPrivateConversation() as List<UiMessage>
                assertTrue("Initial conversation was not restored", restored.containsAll(expected))
            }

            scenario.moveToState(Lifecycle.State.CREATED)
            Thread.sleep(1200L)
            scenario.moveToState(Lifecycle.State.RESUMED)

            scenario.onActivity { activity ->
                val restored = activity.readPrivateConversation() as List<UiMessage>
                assertTrue("Conversation was lost after background/foreground", restored.containsAll(expected))
            }
        } finally {
            scenario.close()
            store.clearConversation()
        }
    }

    @Test
    fun generationServiceSurvivesTaskRemovalConfiguration() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val info = context.packageManager.getServiceInfo(
            ComponentName(context, Deep33GenerationService::class.java),
            0
        )
        assertTrue(
            "Generation service must not stop when the app task is removed",
            info.flags and ServiceInfo.FLAG_STOP_WITH_TASK == 0
        )
        assertTrue(
            "Generation service must run as a foreground service",
            info.foregroundServiceType != 0
        )
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
