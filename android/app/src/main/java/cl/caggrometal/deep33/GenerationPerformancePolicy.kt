package cl.caggrometal.deep33

/**
 * Keeps UI/storage history independent from the model's hot-path context.
 * Older turns remain locally persisted and recoverable; only a bounded recent window
 * is serialized for each inference request.
 */
object GenerationPerformancePolicy {
    const val MODEL_CONTEXT_MAX_MESSAGES = 24
    const val MODEL_CONTEXT_MAX_CHARS = 18_000

    fun selectModelContext(conversation: List<UiMessage>): List<UiMessage> {
        if (conversation.isEmpty()) return emptyList()

        val selectedReversed = ArrayList<UiMessage>(MODEL_CONTEXT_MAX_MESSAGES)
        var chars = 0

        for (message in conversation.asReversed()) {
            if (selectedReversed.size >= MODEL_CONTEXT_MAX_MESSAGES) break

            val nextChars = chars + message.content.length
            if (selectedReversed.isNotEmpty() && nextChars > MODEL_CONTEXT_MAX_CHARS) break

            selectedReversed.add(message)
            chars = nextChars
        }

        selectedReversed.reverse()
        return selectedReversed
    }
}
