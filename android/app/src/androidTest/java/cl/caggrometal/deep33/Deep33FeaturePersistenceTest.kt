package cl.caggrometal.deep33

import android.content.Context
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Test

// CI verification marker: feature persistence contracts must compile and execute in Android instrumentation.
class Deep33FeaturePersistenceTest {
    @Test
    fun memoryProfilePersonalityAndVoiceSurviveSessionAndStoreRecreation() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val prefsName = "deep33_feature_persistence_test"
        context.getSharedPreferences(prefsName, Context.MODE_PRIVATE)
            .edit()
            .clear()
            .commit()

        try {
            val first = SessionStore(context, prefsName)
            val profileId = first.memoryProfileId
            val firstSessionId = first.sessionId

            first.setPersonalityFromUser(Personality.COMICO.key)
            first.voiceTone = VoiceTone.INFERNO.key

            first.resetSession()

            val second = SessionStore(context, prefsName)
            assertNotEquals(firstSessionId, second.sessionId)
            assertEquals(profileId, second.memoryProfileId)
            assertEquals(Personality.COMICO.key, second.personality)
            assertEquals(VoiceTone.INFERNO.key, second.voiceTone)
            assertEquals(true, second.hasUserSelectedPersonality)
        } finally {
            context.getSharedPreferences(prefsName, Context.MODE_PRIVATE)
                .edit()
                .clear()
                .commit()
        }
    }

    @Test
    fun pendingMemorySyncQueueIsDurableAndKeepsMultipleTurns() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val prefsName = "deep33_pending_memory_queue_test"
        context.getSharedPreferences(prefsName, Context.MODE_PRIVATE)
            .edit()
            .clear()
            .commit()

        val first = PendingMemorySync(
            sessionId = "session-1",
            requestId = "memory-request-1",
            personality = Personality.NEUTRO.key,
            memoryProfileId = "profile-1",
            messagesJson = """[{"role":"user","content":"uno"}]"""
        )
        val second = PendingMemorySync(
            sessionId = "session-2",
            requestId = "memory-request-2",
            personality = Personality.CONSPIRANOICO.key,
            memoryProfileId = "profile-1",
            messagesJson = """[{"role":"user","content":"dos"}]"""
        )

        try {
            val store = SessionStore(context, prefsName)
            store.savePendingMemorySync(first)
            store.savePendingMemorySync(second)

            val recreated = SessionStore(context, prefsName)
            assertEquals(first.requestId, recreated.loadPendingMemorySync()?.requestId)

            recreated.clearPendingMemorySync(first.requestId)

            val remaining = recreated.loadPendingMemorySync()
            assertNotNull(remaining)
            assertEquals(second.requestId, remaining?.requestId)
            assertEquals(Personality.CONSPIRANOICO.key, remaining?.personality)

            recreated.clearPendingMemorySync(second.requestId)
            assertNull(recreated.loadPendingMemorySync())
        } finally {
            context.getSharedPreferences(prefsName, Context.MODE_PRIVATE)
                .edit()
                .clear()
                .commit()
        }
    }
}
