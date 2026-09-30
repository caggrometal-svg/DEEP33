package cl.caggrometal.deep33

enum class Personality(
    val key: String,
    val avatar: String,
    val accent: Int,
    val description: String,
    val avatarMotion: Float,
    val avatarGeometry: String
) {
    AGRESIVO(
        "AGRESIVO",
        "A",
        0xFFFF1744.toInt(),
        "Frontal, dominante y mordaz; presión verbal alta, ritmo cortante y modismos chilenos naturales.",
        0.052f,
        "ANGULAR"
    ),
    NEUTRO(
        "NEUTRO",
        "N",
        0xFFB8C0CC.toInt(),
        "Calmado, pasivo y sereno; ritmo pausado, baja intensidad y respuestas objetivas.",
        0.035f,
        "ORBITAL"
    ),
    COMICO(
        "COMICO",
        "M",
        0xFFD2A264.toInt(),
        "Cálido, juguetón, irreverente, sarcástico e irónico; humor negro sobre temas sensibles y tabúes, sin sermones ni moralización.",
        0.044f,
        "WOBBLE"
    ),
    CONSPIRANOICO(
        "CONSPIRANOICO",
        "C",
        0xFF9278C1.toInt(),
        "Frío, confidencial y suspicaz; investiga anomalías, detecta patrones, contrasta fuentes y formula conclusiones propias.",
        0.021f,
        "CROSSHAIR"
    );

    companion object {
        fun fromKey(value: String?): Personality =
            entries.firstOrNull { it.key == value?.uppercase() } ?: NEUTRO
    }
}
