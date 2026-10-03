package cl.caggrometal.deep33

/**
 * Bounds client-side context serialization by request complexity.
 * Stable conversational turns use a small fast window; deeper turns retain more context.
 */
object GenerationPerformancePolicy {
    const val FAST_MAX_MESSAGES = 10
    const val FAST_MAX_CHARS = 7_000
    const val BALANCED_MAX_MESSAGES = 16
    const val BALANCED_MAX_CHARS = 12_000
    const val DEEP_MAX_MESSAGES = 32
    const val DEEP_MAX_CHARS = 24_000

    fun selectModelContext(conversation: List<UiMessage>): List<UiMessage> {
        if (conversation.isEmpty()) return emptyList()
        val latest = conversation.lastOrNull { it.role == "user" }?.content.orEmpty()
        val lowered = latest.lowercase()
        val deep = Regex("\\b(en profundidad|a fondo|muy detallado|paso a paso|explica todo|desarrolla|profundiza|investiga|analiza|compara|evidencia)\\b")
            .containsMatchIn(lowered)
        val complex = deep || latest.length > 700 || latest.count { it == '?' } >= 3

        val maxMessages = when {
            deep -> DEEP_MAX_MESSAGES
            complex -> BALANCED_MAX_MESSAGES
            else -> FAST_MAX_MESSAGES
        }
        val maxChars = when {
            deep -> DEEP_MAX_CHARS
            complex -> BALANCED_MAX_CHARS
            else -> FAST_MAX_CHARS
        }

        val selectedReversed = ArrayList<UiMessage>(maxMessages)
        var chars = 0
        for (message in conversation.asReversed()) {
            if (selectedReversed.size >= maxMessages) break
            val nextChars = chars + message.content.length
            if (selectedReversed.isNotEmpty() && nextChars > maxChars) break
            selectedReversed.add(message)
            chars = nextChars
        }
        selectedReversed.reverse()
        return selectedReversed
    }
}
