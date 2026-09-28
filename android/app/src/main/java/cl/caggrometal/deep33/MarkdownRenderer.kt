package cl.caggrometal.deep33

import android.text.Html
import android.text.Spanned
import android.text.TextUtils

object MarkdownRenderer {
    fun render(markdown: String): Spanned {
        var html = TextUtils.htmlEncode(markdown)
        html = html.replace(Regex("(?m)^###\\s+(.+)$"), "<b>$1</b>")
        html = html.replace(Regex("(?m)^##\\s+(.+)$"), "<big><b>$1</b></big>")
        html = html.replace(Regex("(?m)^#\\s+(.+)$"), "<big><big><b>$1</b></big></big>")
        html = html.replace(Regex("(?m)^[-*]\\s+"), "• ")
        html = html.replace(Regex("""\\[([^]]+)]\\((https?://[^)]+)\\)"""), "<a href=\"$2\">$1</a>")
        val backtick = "\u0060"
        html = html.replace(
            Regex(backtick + "([^" + backtick + "]+)" + backtick),
            "<tt>$1</tt>"
        )
        html = html.replace(Regex("\\*\\*(.+?)\\*\\*"), "<b>$1</b>")
        html = html.replace(Regex("(?<!\\*)\\*([^*]+)\\*(?!\\*)"), "<i>$1</i>")
        html = html.replace("\\n", "<br>")

        @Suppress("DEPRECATION")
        return Html.fromHtml(html)
    }
}
