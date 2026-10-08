package cl.caggrometal.deep33

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class SseTextParserTest {
    @Test
    fun extractsDeltaString() {
        assertEquals(
            "hola",
            SseTextParser.extractText("""{"choices":[{"delta":{"content":"hola"}}]}""")
        )
    }

    @Test
    fun extractsMessageContentArray() {
        assertEquals(
            "hola mundo",
            SseTextParser.extractText(
                """{"choices":[{"message":{"content":[{"type":"text","text":"hola "},{"type":"text","text":"mundo"}]}}]}"""
            )
        )
    }

    @Test
    fun extractsDirectContentArray() {
        assertEquals(
            "respuesta",
            SseTextParser.extractText(
                """{"choices":[{"content":[{"type":"output_text","text":"respuesta"}],"tool_calls":[{"id":"x"}]}]}"""
            )
        )
    }

    @Test
    fun ignoresNonTextToolPayloads() {
        assertNull(
            SseTextParser.extractText(
                """{"choices":[{"delta":{"tool_calls":[{"id":"x"}]}}]}"""
            )
        )
    }
}
