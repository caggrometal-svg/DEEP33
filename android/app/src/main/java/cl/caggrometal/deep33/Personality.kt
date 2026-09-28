package cl.caggrometal.deep33

import android.graphics.Color

enum class Personality(
    val key: String,
    val avatar: String,
    val accent: Int,
    val description: String
) {
    AGRESIVO("AGRESIVO", "A", Color.rgb(255, 23, 68), "Directo, firme y provocador."),
    NEUTRO("NEUTRO", "N", Color.rgb(0, 229, 255), "Equilibrado, profesional y claro."),
    CONSPIRANOICO("CONSPIRANOICO", "C", Color.rgb(179, 136, 255), "Enigmático, tecnológico y analítico.");

    companion object {
        fun fromKey(value: String?): Personality =
            entries.firstOrNull { it.key == value?.uppercase() } ?: NEUTRO
    }
}
