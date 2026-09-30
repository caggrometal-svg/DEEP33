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
            // Lower, firmer and faster: clearly aggressive, with controlled delivery.
            Personality.AGRESIVO -> PersonalityVoiceProfile(personality, 0.90f, 1.10f)
            // Balanced baseline: calm and natural.
            Personality.NEUTRO -> PersonalityVoiceProfile(personality, 0.99f, 0.97f)
            // Brighter, quicker and more animated: unmistakably comic.
            Personality.COMICO -> PersonalityVoiceProfile(personality, 1.06f, 1.06f)
            // Deep, slow and deliberate: restrained, suspicious and measured.
            Personality.CONSPIRANOICO -> PersonalityVoiceProfile(personality, 0.91f, 0.88f)
        }
    }
}
