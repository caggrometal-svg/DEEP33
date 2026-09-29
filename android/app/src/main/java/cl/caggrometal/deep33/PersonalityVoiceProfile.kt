package cl.caggrometal.deep33

/**
 * Personality modifier applied on top of the selected VoiceTone.
 * The selected tone remains the primary voice style; personality only nudges
 * pitch and speaking speed so all three tones stay available in every mode.
 */
data class PersonalityVoiceProfile(
    val personality: Personality,
    val pitchFactor: Float,
    val speechRateFactor: Float
) {
    companion object {
        fun forPersonality(personality: Personality): PersonalityVoiceProfile = when (personality) {
            Personality.AGRESIVO -> PersonalityVoiceProfile(personality, 1.08f, 1.18f)
            Personality.NEUTRO -> PersonalityVoiceProfile(personality, 1.00f, 1.00f)
            Personality.COMICO -> PersonalityVoiceProfile(personality, 1.03f, 1.06f)
            Personality.CONSPIRANOICO -> PersonalityVoiceProfile(personality, 0.97f, 0.92f)
        }
    }
}
