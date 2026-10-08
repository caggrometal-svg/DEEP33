package cl.caggrometal.deep33

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class SseTextParserTest {
    @Test
    fun extractsDeltaContent() {
        val data = """{"choices":[{"delta":{"content":"hola"}}]}"""
        assertEquals("hola", SseTextParser.extractText(data))
    }

    @Test
    fun extractsMessageContentFallback() {
        val data = """{"choices":[{"message":{"content":"respuesta"}}]}"""
        assertEquals("respuesta", SseTextParser.extractText(data))
    }

    @Test
    fun extractsArrayContent() {
        val data = """{"choices":[{"delta":{"content":[{"type":"text","text":"hola "},{"type":"text","text":"mundo"}]}}]}"""
        assertEquals("hola mundo", SseTextParser.extractText(data))
    }

    @Test
    fun extractsRootContent() {
        val data = """{"content":[{"type":"text","text":"raiz"}]}"""
        assertEquals("raiz", SseTextParser.extractText(data))
    }

    @Test
    fun auxiliaryEventWithoutTextIsIgnored() {
        val data = """{"choices":[{"delta":{"tool_calls":[{"id":"1"}]}}]}"""
        assertNull(SseTextParser.extractText(data))
    }

    @Test
    fun invalidPayloadIsIgnored() {
        assertNull(SseTextParser.extractText("not-json"))
    }
}
