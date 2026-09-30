package cl.caggrometal.deep33

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class PersonalityVoiceProfileTest {
    @Test
    fun everyPersonalityHasAnIntentionalAudibleProfile() {
        val neutral = PersonalityVoiceProfile.forPersonality(Personality.NEUTRO)
        val aggressive = PersonalityVoiceProfile.forPersonality(Personality.AGRESIVO)
        val comic = PersonalityVoiceProfile.forPersonality(Personality.COMICO)
        val conspiranoid = PersonalityVoiceProfile.forPersonality(Personality.CONSPIRANOICO)

        assertEquals(1.00f, neutral.pitchFactor, 0.001f)
        assertEquals(1.00f, neutral.speechRateFactor, 0.001f)

        assertEquals(0.74f, aggressive.pitchFactor, 0.001f)
        assertEquals(1.22f, aggressive.speechRateFactor, 0.001f)

        assertEquals(1.18f, comic.pitchFactor, 0.001f)
        assertEquals(1.18f, comic.speechRateFactor, 0.001f)

        assertEquals(0.76f, conspiranoid.pitchFactor, 0.001f)
        assertEquals(0.74f, conspiranoid.speechRateFactor, 0.001f)

        assertTrue(comic.pitchFactor - aggressive.pitchFactor >= 0.40f)
        assertTrue(comic.speechRateFactor - conspiranoid.speechRateFactor >= 0.38f)
    }
}
