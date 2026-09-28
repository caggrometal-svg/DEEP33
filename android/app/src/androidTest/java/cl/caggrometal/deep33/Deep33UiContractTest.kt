package cl.caggrometal.deep33

import org.junit.Assert.assertEquals
import org.junit.Test

class Deep33UiContractTest {
    @Test
    fun personalitiesHaveStableKeys() {
        assertEquals(
            listOf("AGRESIVO", "NEUTRO", "CONSPIRANOICO"),
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
        assertEquals("0%", PersonalityVoiceProfile.forPersonality(Personality.NEUTRO).rate)
        assertEquals("-8%", PersonalityVoiceProfile.forPersonality(Personality.CONSPIRANOICO).rate)
        assertEquals("+18%", PersonalityVoiceProfile.forPersonality(Personality.AGRESIVO).rate)
    }
}
