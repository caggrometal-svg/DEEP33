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
            // Grave y firme, pero dentro de un rango humano para evitar una voz artificial.
            Personality.AGRESIVO -> PersonalityVoiceProfile(personality, 0.96f, 1.02f)
            // Calmado y pasivo: pausado, estable y cercano a una voz humana natural.
            Personality.NEUTRO -> PersonalityVoiceProfile(personality, 1.00f, 0.96f)
            // Cálido y juguetón: variación leve de altura y ritmo conversacional, sin efecto caricaturesco.
            // Android TTS no expone un control directo de "calidez"; aquí se aproxima con pitch/ritmo.
            Personality.COMICO -> PersonalityVoiceProfile(personality, 1.02f, 1.00f)
            // Grave, pausado y confidencial: profundidad moderada para conservar naturalidad.
            Personality.CONSPIRANOICO -> PersonalityVoiceProfile(personality, 0.95f, 0.91f)
        }
    }
}
