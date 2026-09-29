package cl.caggrometal.deep33

enum class VoiceTone(
    val key: String,
    val description: String,
    val pitch: Float,
    val speechRate: Float
) {
    DEEP("DEEP", "Grave y pausado", 0.82f, 0.86f),
    EMBER("EMBER", "Cálido y natural", 1.00f, 0.98f),
    INFERNO("INFERNO", "Intenso y acelerado", 1.12f, 1.10f);

    companion object {
        fun fromKey(value: String?): VoiceTone =
            entries.firstOrNull { it.key == value?.uppercase() } ?: DEEP
    }
}
