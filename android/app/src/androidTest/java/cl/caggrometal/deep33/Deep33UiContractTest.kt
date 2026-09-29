package cl.caggrometal.deep33

import org.junit.Assert.assertEquals
import org.junit.Test

class Deep33UiContractTest {
    @Test
    fun personalitiesHaveStableKeys() {
        assertEquals(
            listOf("AGRESIVO", "NEUTRO", "COMICO", "CONSPIRANOICO"),
            Personality.entries.map { it.key }
        )
    }

    @Test
    fun markdownRendererProducesReadableText() {
        val rendered = MarkdownRenderer.render("**DEEP33**\n" + "`test`").toString()
        assertEquals("DEEP33\ntest", rendered)
    }

    @Test
    fun voiceProfilesAreBounded() {
        assertEquals(1.00f, PersonalityVoiceProfile.forPersonality(Personality.NEUTRO).pitchFactor)
        assertEquals(0.92f, PersonalityVoiceProfile.forPersonality(Personality.CONSPIRANOICO).speechRateFactor)
        assertEquals(1.18f, PersonalityVoiceProfile.forPersonality(Personality.AGRESIVO).speechRateFactor)
        assertEquals(1.06f, PersonalityVoiceProfile.forPersonality(Personality.COMICO).speechRateFactor)
    }
}
