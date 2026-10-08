package cl.caggrometal.deep33

/**
 * Context selection is adaptive rather than tied to fixed FAST/BALANCED/DEEP
 * message-count ceilings. Conversation persistence remains bounded separately.
 */
object GenerationPerformancePolicy {
    private const val RECENT_MESSAGES_TO_PRESERVE = 14
    private const val HARD_CONTEXT_CHARS = 100_000

    // Kept as compatibility constants for older tests/callers; they are no longer
    // used as the primary context-selection policy.
    const val FAST_MAX_MESSAGES = 10
    const val FAST_MAX_CHARS = 7_000
    const val BALANCED_MAX_MESSAGES = 16
    const val BALANCED_MAX_CHARS = 12_000
    const val DEEP_MAX_MESSAGES = 32
    const val DEEP_MAX_CHARS = 24_000

    fun selectModelContext(conversation: List<UiMessage>): List<UiMessage> {
        if (conversation.isEmpty()) return emptyList()
        val all = conversation.takeLast(50)
        val totalChars = all.sumOf { it.content.length }
        if (totalChars <= HARD_CONTEXT_CHARS) return all

        val latest = all.lastOrNull { it.role == "user" }?.content.orEmpty()
        val complex = latest.length > 700 || latest.count { it == '?' } >= 2
        val dynamicBudget = (48_000 + latest.length * if (complex) 10 else 6)
            .coerceAtMost(HARD_CONTEXT_CHARS)

        val recent = all.takeLast(RECENT_MESSAGES_TO_PRESERVE).toMutableList()
        val selected = mutableListOf<UiMessage>()
        val seen = mutableSetOf<Pair<String, String>>()
        var chars = 0

        // Preserve the first turn as continuity anchor when possible.
        all.firstOrNull()?.let {
            selected.add(it)
            seen.add(it.role to it.content)
            chars += it.content.length
        }

        for (message in all.dropLast(RECENT_MESSAGES_TO_PRESERVE).asReversed()) {
            if ((message.role to message.content) in seen) continue
            val next = chars + message.content.length
            if (selected.size > 1 && next + recent.sumOf { it.content.length } > dynamicBudget) break
            selected.add(message)
            seen.add(message.role to message.content)
            chars = next
        }

        selected.addAll(recent.filterNot { (it.role to it.content) in seen })
        return selected.takeLast(50)
    }
}
