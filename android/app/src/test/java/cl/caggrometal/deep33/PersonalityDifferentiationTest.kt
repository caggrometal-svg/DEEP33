package cl.caggrometal.deep33

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class PersonalityDifferentiationTest {
    @Test
    fun everyModeHasDistinctVisualIdentity() {
        val modes = Personality.entries
        assertEquals(4, modes.size)
        assertEquals(4, modes.map { it.accent }.toSet().size)
        assertEquals(4, modes.map { it.avatarMotion }.toSet().size)
        assertEquals(4, modes.map { it.avatarGeometry }.toSet().size)
        assertTrue(modes.all { it.description.isNotBlank() })
    }
}
