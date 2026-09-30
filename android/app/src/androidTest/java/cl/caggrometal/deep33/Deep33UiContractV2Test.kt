package cl.caggrometal.deep33

import org.junit.Assert.assertEquals
import org.junit.Test

class Deep33UiContractV2Test {
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
    fun assistantRendererHidesSourceMetadata() {
        val rendered = MarkdownRenderer.render(
            "Respuesta propia. [Fuente](https://example.com/source)\\n" +
                "Fuentes: https://example.org\\n" +
                "citeturn1search1 [1]",
            suppressAssistantSources = true
        ).toString()

        assertEquals("Respuesta propia.", rendered)
    }

    @Test
    fun voiceProfilesAreBounded() {
        assertEquals(1.00f, PersonalityVoiceProfile.forPersonality(Personality.NEUTRO).pitchFactor)
        assertEquals(0.74f, PersonalityVoiceProfile.forPersonality(Personality.CONSPIRANOICO).speechRateFactor)
        assertEquals(1.22f, PersonalityVoiceProfile.forPersonality(Personality.AGRESIVO).speechRateFactor)
        assertEquals(1.18f, PersonalityVoiceProfile.forPersonality(Personality.COMICO).speechRateFactor)
    }
}
