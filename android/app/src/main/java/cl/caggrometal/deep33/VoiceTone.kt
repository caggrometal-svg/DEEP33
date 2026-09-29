package cl.caggrometal.deep33

enum class VoiceTone(
    val key: String,
    val description: String,
    val pitch: Float,
    val speechRate: Float
) {
    DEEP("DEEP", "Grave, cálido y natural", 0.92f, 0.96f),
    EMBER("EMBER", "Cálido y conversacional", 1.00f, 1.00f),
    INFERNO("INFERNO", "Intenso, pero humano", 1.05f, 1.06f);

    companion object {
        fun fromKey(value: String?): VoiceTone =
            entries.firstOrNull { it.key == value?.uppercase() } ?: EMBER
    }
}
