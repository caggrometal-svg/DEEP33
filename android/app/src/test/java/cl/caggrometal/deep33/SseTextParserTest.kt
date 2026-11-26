package cl.caggrometal.deep33

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.BufferedReader
import java.io.Reader

class SseTextParserTest {
    @Test
    fun parsesLfAndCrlfFrames() {
        val payload = """{"choices":[{"delta":{"content":"Hola"}}]}"""
        for (separator in listOf("\n", "\r\n")) {
            val events = mutableListOf<Pair<String?, String>>()
            SseTextParser.readEvents(
                BufferedReader(java.io.StringReader("event: message${separator}data: $payload${separator}${separator}")),
            ) { type, data -> events += type to data }

            assertEquals(1, events.size)
            assertEquals("message", events.single().first)
            assertEquals("Hola", SseTextParser.extractText(events.single().second, events.single().first))
        }
    }

    @Test
    fun joinsMultipleDataLinesAndHandlesFramesSplitAcrossReads() {
        val payloadPartOne = """{"choices":[{"delta":"""
        val payloadPartTwo = """{"content":"Texto visible"}}]}"""
        val wire = "data: $payloadPartOne\r\ndata: $payloadPartTwo\r\n\r\ndata: [DONE]"
        val events = mutableListOf<Pair<String?, String>>()
        SseTextParser.readEvents(
            BufferedReader(ChunkedReader(wire, 2)),
        ) { type, data -> events += type to data }

        assertEquals(2, events.size)
        assertEquals("Texto visible", SseTextParser.extractText(events[0].second))
        assertEquals("[DONE]", events[1].second)
    }

    @Test
    fun reportsHeartbeatCommentsWithoutCreatingVisibleEvents() {
        var heartbeats = 0
        val events = mutableListOf<Pair<String?, String>>()
        SseTextParser.readEvents(
            BufferedReader(java.io.StringReader(": keep-alive\n\ndata: [DONE]")),
            onHeartbeat = { heartbeats++ },
        ) { type, data -> events += type to data }

        assertEquals(1, heartbeats)
        assertEquals(1, events.size)
        assertEquals("[DONE]", events.single().second)
    }

    @Test
    fun ignoresReasoningStatusAndMarkupAsVisibleText() {
        val reasoning = """{"choices":[{"delta":{"content":"oculto","reasoning":"cadena interna"}}]}"""
        val status = """{"type":"status","status":"searching","content":"buscando"}"""
        val markup = """{"choices":[{"delta":{"content":"<thinking>interno</thinking>"}}]}"""

        assertNull(SseTextParser.extractText(reasoning))
        assertNull(SseTextParser.extractText(status))
        assertNull(SseTextParser.extractText("""{"content":"buscando"}""", "searching"))
        assertNull(SseTextParser.extractText(markup))
    }

    @Test
    fun doneWithoutTextDoesNotProduceVisibleContent() {
        assertNull(SseTextParser.extractText("[DONE]"))
        assertTrue(SseTextParser.extractText("""{"choices":[{"delta":{"content":"  "}}]}""").isNullOrBlank())
    }

    private class ChunkedReader(
        private val value: String,
        private val chunkSize: Int,
    ) : Reader() {
        private var offset = 0

        override fun read(buffer: CharArray, start: Int, length: Int): Int {
            if (offset >= value.length) return -1
            val count = minOf(length, chunkSize, value.length - offset)
            value.toCharArray(buffer, start, offset, offset + count)
            offset += count
            return count
        }

        override fun close() = Unit
    }
}
