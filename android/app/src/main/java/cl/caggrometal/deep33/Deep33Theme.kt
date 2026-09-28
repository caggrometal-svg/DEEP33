package cl.caggrometal.deep33

import androidx.compose.foundation.isSystemInDarkTheme
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
        secondary = accent,
        background = Color(0xFF080A0F),
        surface = Color(0xFF11151D),
        surfaceVariant = Color(0xFF1A202A),
        onBackground = Color(0xFFF2F4F8),
        onSurface = Color(0xFFF2F4F8)
    )
    MaterialTheme(
        colorScheme = scheme,
        content = content
    )
}
