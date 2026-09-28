package cl.caggrometal.deep33

import android.graphics.Color

enum class Personality(
    val key: String,
    val avatar: String,
    val accent: Int,
    val description: String
) {
    AGRESIVO(
        "AGRESIVO",
        "A",
        Color.rgb(255, 23, 68),
        "Directo, impaciente, sarcástico y confrontacional."
    ),
    NEUTRO(
        "NEUTRO",
        "N",
        Color.rgb(0, 229, 255),
        "Analítico, formal, objetivo y basado en datos."
    ),
    CONSPIRANOICO(
        "CONSPIRANOICO",
        "C",
        Color.rgb(179, 136, 255),
        "Teorías, agendas ocultas y anomalías; distingue hechos de hipótesis."
    );

    companion object {
        fun fromKey(value: String?): Personality =
            entries.firstOrNull { it.key == value?.uppercase() } ?: NEUTRO
    }
}
