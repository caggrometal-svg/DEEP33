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
            // Grave, firme y con más empuje: la personalidad debe sentirse físicamente distinta.
            Personality.AGRESIVO -> PersonalityVoiceProfile(personality, 0.84f, 1.05f)
            // Calmado, pasivo y de baja intensidad: más lento y menos invasivo.
            Personality.NEUTRO -> PersonalityVoiceProfile(personality, 0.98f, 0.92f)
            // Cálido y juguetón: ritmo cómodo y una altura menos agresiva que la base anterior.
            // Android TTS no expone un control directo de "calidez"; aquí se aproxima con pitch/ritmo.
            Personality.COMICO -> PersonalityVoiceProfile(personality, 1.00f, 0.95f)
            // Grave, muy pausado y confidencial: sensación de secreto/anomalía sin teatralidad.
            Personality.CONSPIRANOICO -> PersonalityVoiceProfile(personality, 0.88f, 0.86f)
        }
    }
}
