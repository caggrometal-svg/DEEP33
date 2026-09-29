package cl.caggrometal.deep33

/**
 * Personality-specific voice shaping layered over the user's chosen voice tone.
 * Keep the differences audible but natural; avoid extreme pitch/rate that sounds synthetic.
 */
data class PersonalityVoiceProfile(
    val personality: Personality,
    val pitchFactor: Float,
    val speechRateFactor: Float
) {
    companion object {
        fun forPersonality(personality: Personality): PersonalityVoiceProfile = when (personality) {
            // Firmer, slightly lower and quicker delivery.
            Personality.AGRESIVO -> PersonalityVoiceProfile(personality, 0.96f, 1.08f)
            // Balanced baseline: calm and natural.
            Personality.NEUTRO -> PersonalityVoiceProfile(personality, 1.00f, 1.00f)
            // Brighter and more animated, without cartoonish extremes.
            Personality.COMICO -> PersonalityVoiceProfile(personality, 1.08f, 1.04f)
            // Lower, slower and more measured, with a restrained mysterious quality.
            Personality.CONSPIRANOICO -> PersonalityVoiceProfile(personality, 0.93f, 0.92f)
        }
    }
}
