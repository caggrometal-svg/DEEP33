package cl.caggrometal.deep33

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class PersonalityVoiceProfileTest {
    @Test
    fun everyPersonalityHasIntentionalHumanizedShaping() {
        val neutral = PersonalityVoiceProfile.forPersonality(Personality.NEUTRO)
        val aggressive = PersonalityVoiceProfile.forPersonality(Personality.AGRESIVO)
        val comic = PersonalityVoiceProfile.forPersonality(Personality.COMICO)
        val conspiranoid = PersonalityVoiceProfile.forPersonality(Personality.CONSPIRANOICO)

        assertEquals(0.99f, neutral.pitchFactor, 0.001f)
        assertEquals(0.97f, neutral.speechRateFactor, 0.001f)
        assertEquals(0.90f, aggressive.pitchFactor, 0.001f)
        assertEquals(1.10f, aggressive.speechRateFactor, 0.001f)
        assertEquals(1.06f, comic.pitchFactor, 0.001f)
        assertEquals(1.06f, comic.speechRateFactor, 0.001f)
        assertEquals(0.91f, conspiranoid.pitchFactor, 0.001f)
        assertEquals(0.88f, conspiranoid.speechRateFactor, 0.001f)

        assertTrue(comic.pitchFactor > aggressive.pitchFactor)
        assertTrue(aggressive.speechRateFactor > conspiranoid.speechRateFactor)
    }

    @Test
    fun toneAndPersonalityCompositionStaysWithinSafeHumanRange() {
        val profiles = VoiceTone.entries.flatMap { tone ->
            Personality.entries.map { personality ->
                VoiceProfileCalculator.calculate(tone, personality)
            }
        }

        assertEquals(12, profiles.size)
        assertTrue(profiles.all { it.pitch in 0.65f..1.35f })
        assertTrue(profiles.all { it.speechRate in 0.60f..1.45f })

        assertTrue(
            VoiceProfileCalculator.calculate(VoiceTone.DEEP, Personality.CONSPIRANOICO).speechRate <
                VoiceProfileCalculator.calculate(VoiceTone.INFERNO, Personality.AGRESIVO).speechRate
        )
    }
}
