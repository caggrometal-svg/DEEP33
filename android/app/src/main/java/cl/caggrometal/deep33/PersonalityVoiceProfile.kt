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
            Personality.AGRESIVO -> PersonalityVoiceProfile(personality, 0.82f, 1.16f)
            // Balanced baseline: calm and controlled.
            Personality.NEUTRO -> PersonalityVoiceProfile(personality, 1.00f, 0.92f)
            // Brighter and more animated: immediately audible.
            Personality.COMICO -> PersonalityVoiceProfile(personality, 1.12f, 1.12f)
            // Lower, slower and more deliberate: suspicious and investigative.
            Personality.CONSPIRANOICO -> PersonalityVoiceProfile(personality, 0.78f, 0.80f)
        }
    }
}
