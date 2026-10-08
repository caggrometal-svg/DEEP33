package cl.caggrometal.deep33

/**
 * Builds an inference window from the actual conversation size instead of fixed message
 * counts. Older content is compacted only when the current conversation exceeds a safe
 * transport budget; recent and continuity-critical turns remain intact.
 */
object GenerationPerformancePolicy {
    private const val NORMAL_SAFE_CHARS = 48_000
    private const val COMPLEX_SAFE_CHARS = 80_000
    private const val DEEP_SAFE_CHARS = 120_000
    private const val SUMMARY_CHARS = 6_000

    fun selectModelContext(conversation: List<UiMessage>): List<UiMessage> {
        if (conversation.isEmpty()) return emptyList()

        val latestUserIndex = conversation.indexOfLast { it.role == "user" }
        val latestText = if (latestUserIndex >= 0) conversation[latestUserIndex].content else ""
        val lowered = latestText.lowercase()
        val deep = Regex(
            "\\b(en profundidad|a fondo|muy detallado|paso a paso|explica todo|desarrolla|profundiza|investiga|analiza|compara|evidencia)\\b"
        ).containsMatchIn(lowered)
        val complex = deep || latestText.length > 700 || latestText.count { it == '?' } >= 3
        val totalChars = conversation.sumOf { it.content.length }
        val safeChars = when {
            deep -> DEEP_SAFE_CHARS
            complex -> COMPLEX_SAFE_CHARS
            else -> NORMAL_SAFE_CHARS
        }

        if (totalChars <= safeChars) return conversation.toList()

        val selected = ArrayList<Pair<Int, UiMessage>>()
        val included = HashSet<Int>()
        var chars = 0

        fun add(index: Int) {
            if (index !in conversation.indices || index in included) return
            val message = conversation[index]
            if (message.content.isBlank()) return
            if (chars + message.content.length > safeChars && selected.isNotEmpty()) return
            selected.add(index to message)
            included.add(index)
            chars += message.content.length
        }

        conversation.forEachIndexed { index, message ->
            if (message.role == "system") add(index)
        }
        val firstUser = conversation.indexOfFirst { it.role == "user" }
        if (firstUser >= 0) add(firstUser)
        if (latestUserIndex >= 0) add(latestUserIndex)

        for (index in conversation.lastIndex downTo 0) {
            if (index in included) continue
            add(index)
            if (chars >= safeChars) break
        }

        val omitted = conversation.indices.filter {
            it !in included && conversation[it].role in setOf("user", "assistant")
        }
        if (omitted.isNotEmpty()) {
            val summaryParts = ArrayList<String>()
            var remaining = SUMMARY_CHARS
            for (index in omitted) {
                if (remaining <= 0) break
                val message = conversation[index]
                val snippet = message.content
                    .replace(Regex("\\s+"), " ")
                    .trim()
                    .take(remaining.coerceAtMost(1_200))
                if (snippet.isNotBlank()) {
                    summaryParts.add(message.role + ": " + snippet)
                    remaining -= snippet.length + 10
                }
            }
            if (summaryParts.isNotEmpty()) {
                selected.add(
                    -1 to UiMessage(
                        "system",
                        "[CONTEXTO ANTERIOR COMPACTADO]\n" + summaryParts.joinToString("\n")
                    )
                )
            }
        }

        return selected.sortedBy { it.first }.map { it.second }
    }
}