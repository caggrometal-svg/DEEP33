package cl.caggrometal.deep33

import android.content.Context
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class SessionStorePersistenceTest {
    @Test
    fun sessionAndMessagesSurviveStoreRecreation() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val prefsName = "deep33_memory_persistence_test"
        context.getSharedPreferences(prefsName, Context.MODE_PRIVATE)
            .edit()
            .clear()
            .commit()

        try {
            val firstStore = SessionStore(context, prefsName)
            val sessionId = firstStore.sessionId
            firstStore.saveMessages(
                listOf(
                    UiMessage("user", "MEMORY_TEST_USER"),
                    UiMessage("assistant", "MEMORY_TEST_ASSISTANT"),
                )
            )

            val recreatedStore = SessionStore(context, prefsName)
            assertEquals(sessionId, recreatedStore.sessionId)

            val restored = recreatedStore.loadMessages()
            assertEquals(2, restored.size)
            assertEquals("MEMORY_TEST_USER", restored[0].content)
            assertEquals("MEMORY_TEST_ASSISTANT", restored[1].content)
            assertTrue(restored.all { it.role == "user" || it.role == "assistant" })
        } finally {
            context.getSharedPreferences(prefsName, Context.MODE_PRIVATE)
                .edit()
                .clear()
                .commit()
        }
    }
}
