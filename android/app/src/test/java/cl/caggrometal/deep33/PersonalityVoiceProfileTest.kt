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

        assertTrue(aggressive.pitchFactor < neutral.pitchFactor)
        assertTrue(aggressive.speechRateFactor > neutral.speechRateFactor)

        assertTrue(comic.pitchFactor > neutral.pitchFactor)
        assertTrue(comic.speechRateFactor > neutral.speechRateFactor)

        assertTrue(conspiranoid.pitchFactor < neutral.pitchFactor)
        assertTrue(conspiranoid.speechRateFactor < neutral.speechRateFactor)
    }
}
