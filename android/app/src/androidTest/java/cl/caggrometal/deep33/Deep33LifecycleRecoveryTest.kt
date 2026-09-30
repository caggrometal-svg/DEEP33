package cl.caggrometal.deep33

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class Deep33LifecycleRecoveryTest {
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
}
