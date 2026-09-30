package cl.caggrometal.deep33

import android.text.Html
import android.text.Spanned
import android.text.TextUtils

object MarkdownRenderer {
    fun render(markdown: String, suppressAssistantSources: Boolean = false): Spanned {
        val input = if (suppressAssistantSources) sanitizeAssistantSources(markdown) else markdown
        var html = TextUtils.htmlEncode(input)
        html = html.replace(Regex("(?m)^###\\s+(.+)$"), "<b>$1</b>")
        html = html.replace(Regex("(?m)^##\\s+(.+)$"), "<big><b>$1</b></big>")
        html = html.replace(Regex("(?m)^#\\s+(.+)$"), "<big><big><b>$1</b></big></big>")
        html = html.replace(Regex("(?m)^[-*]\\s+"), "• ")
        html = html.replace(Regex("""\\[([^]]+)\\]\\((https?://[^)]+)\\)"""), "<a href=\"$2\">$1</a>")
        val backtick = "\u0060"
        html = html.replace(
            Regex(backtick + "([^" + backtick + "]+)" + backtick),
            "<tt>$1</tt>"
        )
        html = html.replace(Regex("\\*\\*(.+?)\\*\\*"), "<b>$1</b>")
        html = html.replace(Regex("(?<!\\*)\\*([^*]+)\\*(?!\\*)"), "<i>$1</i>")
        html = html
            .replace("\r\n", "\n")
            .replace("\r", "\n")
            .replace("\\n", "<br>")
            .replace("\n", "<br>")

        @Suppress("DEPRECATION")
        return Html.fromHtml(html)
    }

    private fun sanitizeAssistantSources(markdown: String): String {
        var value = markdown

        // Normalize provider/proxy escaped line breaks before source-section matching.
        value = value.replace("\\n", "\n").replace("\\r", "\r")

        // Defense in depth: the backend removes source metadata, but assistant text
        // is also sanitized immediately before rendering on the Android UI.
        value = value.replace(Regex("(?s)(?:cite|url).*?"), "")
        value = value.replace(Regex("(?is)<a\\b[^>]*>.*?</a>"), "")
        // Hide search-provider citation IDs (for example, 【turn0search0】) in visible answers.
        value = value.replace(
            Regex("【\\s*turn\\d+(?:search|news|reddit|fetch|image|product|business)\\d+\\s*】", RegexOption.IGNORE_CASE),
            ""
        )
        value = value.replace(Regex("\\(turn\\d+(?:search|news|reddit|fetch)\\d+\\)"), "")
        value = value.replace(
            Regex(
                "(?im)^\\s*(?:#{0,6}\\s*)?" +
                    "(?:fuente|sources?|referencias?|references?|cita|citations?)" +
                    "\\s*(?:#?\\d+)?\\s*[:\\-–]?\\s*(?:\\n|$).*\\z"
            ),
            ""
        )
        // Remove inline source labels such as "Fuentes: <url>" before URL stripping,
        // otherwise an empty label can survive as visible UI text.
        value = value.replace(
            Regex(
                "(?im)^\\s*(?:#{0,6}\\s*)?" +
                    "(?:fuente|sources?|referencias?|references?|cita|citations?)" +
                    "\\s*(?:#?\\d+)?\\s*[:\\-–]\\s*.*$"
            ),
            ""
        )
        value = value.replace(
            Regex("(?im)^\\s*(?:retrieved from|consultado en|recuperado de)\\s+https?://\\S+\\s*$"),
            ""
        )
        value = value.replace(
            Regex("(?i)\\[(?:fuente|source|ref(?:erencia)?|citation|cita)\\s*:?[^\\]]{0,180}\\]"),
            ""
        )
        value = value.replace(
            Regex("(?<!\\w)\\[\\^?\\d{1,3}(?:\\s*[,;]\\s*\\^?\\d{1,3})*\\](?!\\()"),
            ""
        )
        // Some providers use full-width citation markers.
        value = value.replace(
            Regex("[【〖]\\s*\\^?\\d{1,3}(?:\\s*[,;]\\s*\\^?\\d{1,3})*\\s*[】〗]"),
            ""
        )
        value = value.replace(
            Regex("\\[[^\\]]+\\]\\(https?://[^)]+\\)"),
            ""
        )
        // Remove empty URL parentheses left after a source markdown link is stripped.
        value = value.replace(Regex("\\(\\s*\\)"), "")
        // A source heading without a body is still metadata and must not reach the user.
        value = value.replace(
            Regex("(?im)^\\s*(?:fuentes?|sources?|referencias?|references?)\\s*:?\\s*$"),
            ""
        )
        value = value.replace(Regex("(?i)https?://[^\\s)\\]>]+"), "")
        value = value.replace(Regex("[ \\t]{2,}"), " ")
        value = value.replace(Regex(" *\\n *\\n *"), "\n\n")
        return value.trim()
    }
}
