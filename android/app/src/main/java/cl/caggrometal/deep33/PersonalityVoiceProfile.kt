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
            // Lower, firmer and faster: immediately distinguishable from neutral.
            Personality.AGRESIVO -> PersonalityVoiceProfile(personality, 0.78f, 1.18f)
            // Balanced baseline: calm and natural.
            Personality.NEUTRO -> PersonalityVoiceProfile(personality, 1.00f, 1.00f)
            // Higher, faster and more animated: clearly comic in delivery.
            Personality.COMICO -> PersonalityVoiceProfile(personality, 1.20f, 1.16f)
            // Deep, slower and deliberate: clearly suspicious and measured.
            Personality.CONSPIRANOICO -> PersonalityVoiceProfile(personality, 0.80f, 0.78f)
        }
    }
}
