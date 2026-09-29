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
        Color.rgb(255, 42, 68),
        "Directo, impaciente, sarcástico y confrontacional."
    ),
    NEUTRO(
        "NEUTRO",
        "N",
        Color.rgb(0, 255, 140),
        "Analítico, formal, objetivo y basado en datos."
    ),
    CONSPIRANOICO(
        "CONSPIRANOICO",
        "C",
        Color.rgb(255, 92, 70),
        "Teorías, agendas ocultas y anomalías; distingue hechos de hipótesis."
    );

    companion object {
        fun fromKey(value: String?): Personality =
            entries.firstOrNull { it.key == value?.uppercase() } ?: NEUTRO
    }
}
