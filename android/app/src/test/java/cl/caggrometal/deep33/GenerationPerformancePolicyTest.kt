package cl.caggrometal.deep33

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class GenerationPerformancePolicyTest {
    @Test
    fun selectsOnlyRecentMessagesByCount() {
        val input = (1..40).map { UiMessage("user", "message-$it") }

        val selected = GenerationPerformancePolicy.selectModelContext(input)

        assertEquals(GenerationPerformancePolicy.FAST_MAX_MESSAGES, selected.size)
        assertEquals("message-31", selected.first().content)
        assertEquals("message-40", selected.last().content)
    }

    @Test
    fun preservesNewestTurnsUnderCharacterBudget() {
        val input = (1..30).map { UiMessage("user", "x".repeat(1_000) + "-$it") }

        val selected = GenerationPerformancePolicy.selectModelContext(input)

        assertEquals(11, selected.size)
        assertEquals("x".repeat(1_000) + "-30", selected.last().content)
        assertTrue(selected.sumOf { it.content.length } <= GenerationPerformancePolicy.BALANCED_MAX_CHARS)
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
