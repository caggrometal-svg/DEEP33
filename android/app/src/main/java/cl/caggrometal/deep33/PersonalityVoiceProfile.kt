package cl.caggrometal.deep33

/**
 * Client-side voice profile mirrored by the DEEP33 edge-tts backend.
 * Values are intentionally bounded so personality changes remain intelligible.
 */
data class PersonalityVoiceProfile(
    val personality: Personality,
    val rate: String,
    val pitch: String
) {
    companion object {
        fun forPersonality(personality: Personality): PersonalityVoiceProfile = when (personality) {
            Personality.AGRESIVO -> PersonalityVoiceProfile(personality, "+18%", "+8Hz")
            Personality.NEUTRO -> PersonalityVoiceProfile(personality, "0%", "0Hz")
            Personality.COMICO -> PersonalityVoiceProfile(personality, "+6%", "+3Hz")
            Personality.CONSPIRANOICO -> PersonalityVoiceProfile(personality, "-8%", "-4Hz")
        }
    }
}
