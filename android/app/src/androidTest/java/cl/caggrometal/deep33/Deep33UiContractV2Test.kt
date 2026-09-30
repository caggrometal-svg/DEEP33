package cl.caggrometal.deep33

import android.app.Activity
import android.widget.Button
import androidx.test.core.app.ActivityScenario
import org.junit.Assert.assertEquals
import org.junit.Test

class Deep33UiContractV2Test {
    @Test
    fun stopControlUsesDeep33SquareGlyph() {
        val scenario = ActivityScenario.launch(MainActivity::class.java)
        try {
            scenario.onActivity { activity: Activity ->
                val field = activity.javaClass.getDeclaredField("cancelButton").apply { isAccessible = true }
                val button = field.get(activity) as Button
                assertEquals("▪", button.text.toString())
                assertEquals("Detener generación", button.contentDescription.toString())
            }
        } finally {
            scenario.close()
        }
    }

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

        val escapedRendered = MarkdownRenderer.render(
            "Respuesta propia.\\nFuentes:\\n- Example https://example.com\\n【1】",
            suppressAssistantSources = true
        ).toString()
        assertEquals("Respuesta propia.", escapedRendered)
    }

    @Test
    fun generationStopControlUsesThemedSquareGlyph() {
        assertEquals("▪️", MainActivity.STOP_BUTTON_GLYPH)
    }

    @Test
    fun voiceProfilesAreBounded() {
        assertEquals(1.00f, PersonalityVoiceProfile.forPersonality(Personality.NEUTRO).pitchFactor)
        assertEquals(0.74f, PersonalityVoiceProfile.forPersonality(Personality.CONSPIRANOICO).speechRateFactor)
        assertEquals(1.22f, PersonalityVoiceProfile.forPersonality(Personality.AGRESIVO).speechRateFactor)
        assertEquals(1.18f, PersonalityVoiceProfile.forPersonality(Personality.COMICO).speechRateFactor)
    }
}
