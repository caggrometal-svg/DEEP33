package cl.caggrometal.deep33

data class AppliedVoiceProfile(
    val pitch: Float,
    val speechRate: Float
)

object VoiceProfileCalculator {
    private const val MIN_PITCH = 0.65f
    private const val MAX_PITCH = 1.35f
    private const val MIN_RATE = 0.60f
    private const val MAX_RATE = 1.45f

    fun calculate(tone: VoiceTone, personality: Personality): AppliedVoiceProfile {
        val personalityProfile = PersonalityVoiceProfile.forPersonality(personality)
        return AppliedVoiceProfile(
            pitch = (tone.pitch * personalityProfile.pitchFactor).coerceIn(MIN_PITCH, MAX_PITCH),
            speechRate = (tone.speechRate * personalityProfile.speechRateFactor)
                .coerceIn(MIN_RATE, MAX_RATE)
        )
    }
}
