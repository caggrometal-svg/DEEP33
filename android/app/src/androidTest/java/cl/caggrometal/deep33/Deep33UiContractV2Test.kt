package cl.caggrometal.deep33

import android.app.Activity
import android.view.View
import android.widget.Button
import android.widget.TextView
import androidx.test.core.app.ActivityScenario
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class Deep33UiContractV2Test {
    @Test
    fun stopControlUsesDeep33SquareGlyph() {
        val scenario = ActivityScenario.launch(MainActivity::class.java)
        try {
            scenario.onActivity { activity: Activity ->
                val field = activity.javaClass.getDeclaredField("cancelButton").apply { isAccessible = true }
                val button = field.get(activity) as Button
                assertEquals(MainActivity.STOP_BUTTON_GLYPH, button.text.toString())
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
    fun voiceContractHasThreeTonesAndSafeProfiles() {
        assertEquals(3, VoiceTone.entries.size)
        val profiles = VoiceTone.entries.flatMap { tone ->
            Personality.entries.map { personality ->
                VoiceProfileCalculator.calculate(tone, personality)
            }
        }
        assertEquals(12, profiles.size)
        assertTrue(profiles.all { it.pitch in 0.65f..1.35f })
        assertTrue(profiles.all { it.speechRate in 0.60f..1.45f })
    }

    @Test
    fun personalityProfilesRemainVisiblyDistinct() {
        val descriptions = Personality.entries.map { it.description }
        val voiceProfiles = Personality.entries.map { PersonalityVoiceProfile.forPersonality(it) }
        assertEquals(Personality.entries.size, descriptions.distinct().size)
        assertEquals(Personality.entries.size, voiceProfiles.map { it.pitchFactor to it.speechRateFactor }.distinct().size)
    }

    fun headerExposesDeep33IdentityAndActivePersonality() {
        val scenario = ActivityScenario.launch(MainActivity::class.java)
        try {
            scenario.onActivity { activity: Activity ->
                val personalityField = activity.javaClass
                    .getDeclaredField("currentPersonalityView")
                    .apply { isAccessible = true }
                val personalityView = personalityField.get(activity) as TextView

                assertTrue(personalityView.text.toString().contains("NEUTRO"))
                assertNull(findTextView(activity, "DEEP33"))
            }
        } finally {
            scenario.close()
        }
    }

    @Test
    fun voiceModeIsAudioOnlyInUiContract() {
        val scenario = ActivityScenario.launch(MainActivity::class.java)
        try {
            scenario.onActivity { activity: Activity ->
                val voicePanel = privateView(activity, "voicePanel")
                val chatScroll = privateView(activity, "chatScroll")
                val composer = privateView(activity, "composer")
                val setVoiceModeUi = activity.javaClass
                    .getDeclaredMethod("setVoiceModeUi", Boolean::class.javaPrimitiveType!!)
                    .apply { isAccessible = true }

                setVoiceModeUi.invoke(activity, true)

                assertEquals(View.VISIBLE, voicePanel.visibility)
                assertEquals(View.GONE, chatScroll.visibility)
                assertEquals(View.GONE, composer.visibility)
                assertTrue(activity.javaClass.getDeclaredField("voiceModeActive")
                    .apply { isAccessible = true }
                    .getBoolean(activity))
            }
        } finally {
            scenario.close()
        }
    }

    private fun privateView(activity: Activity, fieldName: String): View {
        val field = activity.javaClass.getDeclaredField(fieldName).apply { isAccessible = true }
        return field.get(activity) as View
    }

    private fun findTextView(activity: Activity, text: String): TextView? {
        val root = activity.window.decorView
        return findTextViewRecursive(root, text)
    }

    private fun findTextViewRecursive(view: View, text: String): TextView? {
        if (view is TextView && view.text?.toString() == text) return view
        if (view is android.view.ViewGroup) {
            for (i in 0 until view.childCount) {
                val match = findTextViewRecursive(view.getChildAt(i), text)
                if (match != null) return match
            }
        }
        return null
    }
}
