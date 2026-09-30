package cl.caggrometal.deep33

enum class VoiceTone(
    val key: String,
    val description: String,
    val pitch: Float,
    val speechRate: Float
) {
    DEEP("DEEP", "Grave, íntimo y nocturno", 0.90f, 0.94f),
    EMBER("EMBER", "Cálido, firme y conversacional", 0.99f, 0.99f),
    INFERNO("INFERNO", "Contundente, áspero y enérgico", 1.05f, 1.09f);

    companion object {
        fun fromKey(value: String?): VoiceTone =
            entries.firstOrNull { it.key == value?.uppercase() } ?: EMBER
    }
}
