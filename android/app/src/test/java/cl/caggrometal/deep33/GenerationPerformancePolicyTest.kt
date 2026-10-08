package cl.caggrometal.deep33

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class GenerationPerformancePolicyTest {
    @Test
    fun preservesFullConversationWhenItFitsSafeWindow() {
        val input = (1..40).map { UiMessage("user", "message-$it") }

        val selected = GenerationPerformancePolicy.selectModelContext(input)

        assertEquals(input, selected)
    }

    @Test
    fun compactsOversizedHistoryInsteadOfSilentlyDroppingIt() {
        val input = (1..90).map { UiMessage("user", "x".repeat(1_500) + "-$it") }

        val selected = GenerationPerformancePolicy.selectModelContext(input)

        assertTrue(selected.any { it.role == "system" && it.content.contains("CONTEXTO ANTERIOR COMPACTADO") })
        assertEquals(input.last().content, selected.last().content)
        assertTrue(selected.sumOf { it.content.length } > 0)
    }

    @Test
    fun keepsChronologicalOrder() {
        val input = listOf(
            UiMessage("user", "one"),
            UiMessage("assistant", "two"),
            UiMessage("user", "three"),
            UiMessage("assistant", "four")
        )

        val selected = GenerationPerformancePolicy.selectModelContext(input)

        assertEquals(listOf("one", "two", "three", "four"), selected.map { it.content })
    }

    @Test
    fun failoverPolicyRemainsIdempotentForPost() {
        assertTrue(
            Deep33FailoverPolicy.canFailover(
                method = "POST",
                requestBodyStarted = true,
                error = Deep33ApiException.Kind.NETWORK,
                idempotentRequest = true
            )
        )
    }
}
