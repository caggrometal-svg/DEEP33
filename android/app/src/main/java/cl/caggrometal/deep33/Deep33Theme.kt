package cl.caggrometal.deep33

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

@Composable
fun Deep33Theme(
    personality: Personality,
    content: @Composable () -> Unit
) {
    val accent = Color(personality.accent)
    val scheme = darkColorScheme(
        primary = accent,
        secondary = Color(0xFFFF2A44),
        tertiary = Color(0xFF00FF8C),
        background = Color(0xFF050607),
        surface = Color(0xFF101317),
        surfaceVariant = Color(0xFF181C21),
        onBackground = Color(0xFFF2F4F8),
        onSurface = Color(0xFFF2F4F8)
    )
    MaterialTheme(colorScheme = scheme, content = content)
}
