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
        "Directo, impaciente, sarcástico y confrontacional.",
        0.052f,
        "ANGULAR"
    ),
    NEUTRO(
        "NEUTRO",
        "N",
        0xFFB8C0CC.toInt(),
        "Analítico, formal, objetivo y basado en datos.",
        0.035f,
        "ORBITAL"
    ),
    COMICO(
        "COMICO",
        "M",
        0xFFD2A264.toInt(),
        "Irreverente, ingenioso, sarcástico y de humor oscuro, sin perder precisión.",
        0.044f,
        "WOBBLE"
    ),
    CONSPIRANOICO(
        "CONSPIRANOICO",
        "C",
        0xFF9278C1.toInt(),
        "Teorías, agendas ocultas y anomalías; distingue hechos de hipótesis.",
        0.021f,
        "CROSSHAIR"
    );

    companion object {
        fun fromKey(value: String?): Personality =
            entries.firstOrNull { it.key == value?.uppercase() } ?: NEUTRO
    }
}
