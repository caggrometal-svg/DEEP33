package cl.caggrometal.deep33

/**
 * Personality-specific voice shaping layered over the user's chosen voice tone.
 * Profiles are intentionally separated so personality changes are immediately audible.
 */
data class PersonalityVoiceProfile(
    val personality: Personality,
    val pitchFactor: Float,
    val speechRateFactor: Float
) {
    companion object {
        fun forPersonality(personality: Personality): PersonalityVoiceProfile = when (personality) {
            // Distinct but still plausible on common Android TTS engines.
            // Aggressive: lower pitch, tighter tempo, less "synthetic announcer" cadence.
            Personality.AGRESIVO -> PersonalityVoiceProfile(personality, 0.69f, 1.17f)
            // Neutral: natural reference voice.
            Personality.NEUTRO -> PersonalityVoiceProfile(personality, 1.00f, 1.00f)
            // Comic: brighter pitch with a slightly livelier tempo.
            Personality.COMICO -> PersonalityVoiceProfile(personality, 1.15f, 1.13f)
            // Conspiranoic: noticeably lower and slower, with deliberate spacing.
            Personality.CONSPIRANOICO -> PersonalityVoiceProfile(personality, 0.72f, 0.80f)
        }
    }
}
