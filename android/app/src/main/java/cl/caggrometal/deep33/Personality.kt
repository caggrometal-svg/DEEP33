package cl.caggrometal.deep33

enum class Personality(
    val key: String,
    val avatar: String,
    val accent: Int,
    val description: String,
    val voiceStyle: String,
    val uiTag: String
) {
    AGRESIVO(
        "AGRESIVO",
        "A",
        0xFFFF1744.toInt(),
        "Directo, impaciente, sarcástico y confrontacional.",
        "grave, firme y seco",
        "DIRECTO"
    ),
    NEUTRO(
        "NEUTRO",
        "N",
        0xFFB8C0CC.toInt(),
        "Analítico, formal, objetivo y basado en datos.",
        "sereno, cálido y preciso",
        "PRECISO"
    ),
    COMICO(
        "COMICO",
        "M",
        0xFFD2A264.toInt(),
        "Irreverente, ingenioso, sarcástico y de humor oscuro, sin perder precisión.",
        "ágil, expresivo y ligeramente brillante",
        "IRONICO"
    ),
    CONSPIRANOICO(
        "CONSPIRANOICO",
        "C",
        0xFF9278C1.toInt(),
        "Teorías, agendas ocultas y anomalías; distingue hechos de hipótesis.",
        "bajo, pausado y conspirativo",
        "ANOMALIAS"
    );

    companion object {
        fun fromKey(value: String?): Personality =
            entries.firstOrNull { it.key == value?.uppercase() } ?: NEUTRO
    }
}
