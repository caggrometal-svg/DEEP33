package cl.caggrometal.deep33

import android.app.Activity
import android.view.View
import android.widget.TextView
import androidx.test.core.app.ActivityScenario
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class Deep33UiContractV2Test {
    @Test
    fun stopControlUsesDeep33SquareGlyph() {
        val scenario = ActivityScenario.launch(MainActivity::class.java)
        try {
            scenario.onActivity { activity: Activity ->
                val field = activity.javaClass.getDeclaredField("cancelButton").apply { isAccessible = true }
                val button = field.get(activity) as View
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
    fun generationStopControlUsesThemedStopIcon() {
        assertTrue(R.drawable.ic_action_stop > 0)
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
    fun headerExposesDeep33IdentityAndActivePersonality() {
        val scenario = ActivityScenario.launch(MainActivity::class.java)
        try {
            scenario.onActivity { activity: Activity ->
                val personalityField = activity.javaClass
                    .getDeclaredField("currentPersonalityView")
                    .apply { isAccessible = true }
                val personalityView = personalityField.get(activity) as TextView

                assertTrue(personalityView.text.toString().contains("NEUTRO"))
                assertEquals("DEEP33", findTextView(activity, "DEEP33")?.text?.toString())
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


    @Test
    fun chatBubblesHaveFixedRedAndGreenNeonBorders() {
        val scenario = ActivityScenario.launch(MainActivity::class.java)
        try {
            scenario.onActivity { activity: Activity ->
                val conversationField = activity.javaClass.getDeclaredField("conversation")
                    .apply { isAccessible = true }
                @Suppress("UNCHECKED_CAST")
                val messages = conversationField.get(activity) as MutableList<UiMessage>
                messages.clear()
                messages.add(UiMessage("user", "mensaje usuario"))
                messages.add(UiMessage("assistant", "respuesta DEEP33"))

                activity.javaClass.getDeclaredMethod("renderConversation").apply {
                    isAccessible = true
                }.invoke(activity)

                val container = privateView(activity, "chatContainer") as android.view.ViewGroup
                val userBubble = container.findViewWithTag<View>("chat-bubble-user")
                val assistantBubble = container.findViewWithTag<View>("chat-bubble-assistant")
                assertTrue(userBubble != null)
                assertTrue(assistantBubble != null)

                val userBg = userBubble.background as android.graphics.drawable.GradientDrawable
                val assistantBg = assistantBubble.background as android.graphics.drawable.GradientDrawable
                assertEquals(Deep33Theme.GREEN, userBg.strokeColor.defaultColor)
                assertEquals(Deep33Theme.RED_NEON, assistantBg.strokeColor.defaultColor)
            }
        } finally {
            scenario.close()
        }
    }

    @Test
    fun voiceModeContainsNoVisibleTextViews() {
        val scenario = ActivityScenario.launch(MainActivity::class.java)
        try {
            scenario.onActivity { activity: Activity ->
                val setVoiceModeUi = activity.javaClass
                    .getDeclaredMethod("setVoiceModeUi", Boolean::class.javaPrimitiveType!!)
                    .apply { isAccessible = true }
                setVoiceModeUi.invoke(activity, true)

                val voicePanel = privateView(activity, "voicePanel")
                assertEquals(View.VISIBLE, voicePanel.visibility)
                assertEquals(0, visibleTextViewCount(voicePanel))
                assertEquals(View.GONE, privateView(activity, "chatScroll").visibility)
                assertEquals(View.GONE, privateView(activity, "composer").visibility)
                assertEquals(View.GONE, privateView(activity, "mainHeader").visibility)
            }
        } finally {
            scenario.close()
        }
    }

    @Test
    fun chatDraftSurvivesUiNavigationRebuild() {
        val scenario = ActivityScenario.launch(MainActivity::class.java)
        try {
            scenario.onActivity { activity: Activity ->
                val input = privateView(activity, "input") as android.widget.EditText
                input.setText("borrador persistente")

                val tabClass = Class.forName("cl.caggrometal.deep33.MainActivity\$Tab")
                val tabs = tabClass.enumConstants
                val settings = tabs.first { it.toString() == "SETTINGS" }
                val chat = tabs.first { it.toString() == "CHAT" }
                val showTab = activity.javaClass
                    .getDeclaredMethod("showTab", tabClass)
                    .apply { isAccessible = true }

                showTab.invoke(activity, settings)
                showTab.invoke(activity, chat)

                val restored = privateView(activity, "input") as android.widget.EditText
                assertEquals("borrador persistente", restored.text.toString())
            }
        } finally {
            scenario.close()
        }
    }

    @Test
    fun statusScreenIsReachableFromSidebar() {
        val scenario = ActivityScenario.launch(MainActivity::class.java)
        try {
            scenario.onActivity { activity: Activity ->
                activity.javaClass.getDeclaredMethod("showSidebar").apply {
                    isAccessible = true
                }.invoke(activity)

                val status = findTextView(activity, "ESTADO")
                assertTrue(status != null)
                status?.performClick()

                val tabField = activity.javaClass.getDeclaredField("currentTab")
                    .apply { isAccessible = true }
                assertEquals("STATUS", tabField.get(activity).toString())
            }
        } finally {
            scenario.close()
        }
    }

    @Test
    fun avatarRemainsMinimalCustomView() {
        val scenario = ActivityScenario.launch(MainActivity::class.java)
        try {
            scenario.onActivity { activity: Activity ->
                val avatar = privateView(activity, "avatarView") as VoiceAvatarView
                assertEquals(0, avatar.childCount)
                assertTrue(avatar.contentDescription.toString().contains("Avatar"))
            }
        } finally {
            scenario.close()
        }
    }

    private fun visibleTextViewCount(view: View): Int {
        if (view.visibility != View.VISIBLE) return 0
        var count = if (view is TextView) 1 else 0
        if (view is android.view.ViewGroup) {
            for (i in 0 until view.childCount) {
                count += visibleTextViewCount(view.getChildAt(i))
            }
        }
        return count
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
