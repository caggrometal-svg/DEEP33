package cl.caggrometal.deep33

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class Deep33BasePersistenceTest {
    @Test
    fun sessionAndMessagesSurviveStoreRecreation() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val first = SessionStore(context, "deep33_phase3_test")
        first.resetSession()
        val sessionId = first.sessionId
        first.saveMessages(
            listOf(
                UiMessage("user", "Hola DEEP33"),
                UiMessage("assistant", "DEEP33 OK")
            )
        )

        val second = SessionStore(context, "deep33_phase3_test")
        assertEquals(sessionId, second.sessionId)
        val restored = second.loadMessages()
        assertEquals(2, restored.size)
        assertEquals("Hola DEEP33", restored[0].content)
        assertEquals("DEEP33 OK", restored[1].content)
        assertTrue(restored.all { it.role == "user" || it.role == "assistant" })

        second.resetSession()
        assertEquals(0, second.loadMessages().size)
        assertNotEquals(sessionId, second.sessionId)
    }
}
