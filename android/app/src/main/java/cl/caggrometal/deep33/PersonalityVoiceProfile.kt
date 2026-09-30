package cl.caggrometal.deep33

import java.util.Locale

/**
 * Personality-specific voice shaping layered over the user's chosen voice tone.
 * These are cadence and voice-selection hints, not claims about a specific installed voice.
 */
data class PersonalityVoiceProfile(
    val personality: Personality,
    val pitchFactor: Float,
    val speechRateFactor: Float,
    val preferredLocales: List<Locale>,
) {
    companion object {
        fun forPersonality(personality: Personality): PersonalityVoiceProfile = when (personality) {
            Personality.AGRESIVO -> PersonalityVoiceProfile(
                personality, 0.80f, 1.10f,
                listOf(Locale("es", "CL"), Locale("es", "ES"), Locale("es", "MX"))
            )
            Personality.NEUTRO -> PersonalityVoiceProfile(
                personality, 0.98f, 0.97f,
                listOf(Locale("es", "CL"), Locale("es", "ES"))
            )
            Personality.COMICO -> PersonalityVoiceProfile(
                personality, 1.08f, 1.06f,
                listOf(Locale("es", "MX"), Locale("es", "CL"), Locale("es", "ES"))
            )
            Personality.CONSPIRANOICO -> PersonalityVoiceProfile(
                personality, 0.84f, 0.86f,
                listOf(Locale("es", "CL"), Locale("es", "ES"))
            )
        }
    }
}
