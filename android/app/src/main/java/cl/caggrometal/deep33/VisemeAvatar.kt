package cl.caggrometal.deep33

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.size
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.unit.dp
import kotlin.math.sin

/**
 * Zero-network-latency avatar. The viseme value is rendered locally on Canvas.
 * A future audio analyser can update viseme (0f..1f) without changing this renderer.
 */
@Composable
fun VisemeAvatar(
    personality: Personality,
    viseme: Float,
    modifier: Modifier = Modifier.size(168.dp)
) {
    val clamped = viseme.coerceIn(0f, 1f)
    var pulse by remember { mutableFloatStateOf(0f) }
    pulse = (sin(clamped * Math.PI).toFloat()).coerceIn(0f, 1f)

    Canvas(modifier = modifier) {
        val center = Offset(size.width / 2f, size.height / 2f)
        val radius = size.minDimension * 0.36f
        val accent = Color(personality.accent)

        drawCircle(
            color = Color(0xFF10151F),
            radius = radius,
            center = center
        )
        drawCircle(
            color = accent.copy(alpha = 0.75f),
            radius = radius,
            center = center,
            style = Stroke(width = 4f)
        )

        val eyeY = center.y - radius * 0.18f
        val eyeOffset = radius * 0.38f
        drawCircle(Color.White, radius * 0.055f, Offset(center.x - eyeOffset, eyeY))
        drawCircle(Color.White, radius * 0.055f, Offset(center.x + eyeOffset, eyeY))

        val mouthWidth = radius * (0.62f + 0.12f * pulse)
        val mouthHeight = radius * (0.06f + 0.24f * clamped)
        drawRoundRect(
            color = accent,
            topLeft = Offset(center.x - mouthWidth, center.y + radius * 0.28f),
            size = Size(mouthWidth * 2f, mouthHeight),
            cornerRadius = androidx.compose.ui.geometry.CornerRadius(mouthHeight / 2f),
            style = Stroke(width = 5f, cap = StrokeCap.Round)
        )
    }
}
