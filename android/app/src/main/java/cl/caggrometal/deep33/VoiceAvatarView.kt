package cl.caggrometal.deep33

import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.Path
import android.graphics.RectF
import android.util.AttributeSet
import android.view.View
import kotlin.math.min

enum class AvatarState { IDLE, LISTENING, THINKING, SPEAKING }

class VoiceAvatarView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null
) : View(context, attrs) {
    private val fillPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.FILL
    }
    private val strokePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeCap = Paint.Cap.ROUND
    }

    private var personality = Personality.NEUTRO
    private var state = AvatarState.IDLE

    fun setPersonality(value: Personality) {
        personality = value
        invalidate()
    }

    fun setVoiceState(value: AvatarState) {
        state = value
        invalidate()
    }

    fun setAudioLevel(value: Float) {
        // Audio level is intentionally ignored visually. Voice mode stays expressive
        // through state text rather than motion, pulsing, or mouth scaling.
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)

        val size = min(width, height).toFloat()
        val centerX = width / 2f
        val centerY = height / 2f
        val radius = size * 0.29f
        val accent = personality.accent

        // One static round face, one contour, two eyes, brows and mouth.
        // No pulse, orbit, wobble, blink, scale or audio-reactive effect.
        fillPaint.color = 0xFF0A0A0A.toInt()
        canvas.drawCircle(centerX, centerY, radius + dp(2f), fillPaint)

        strokePaint.color = withAlpha(accent, 205)
        strokePaint.strokeWidth = dp(2f)
        canvas.drawCircle(centerX, centerY, radius + dp(2f), strokePaint)

        drawStaticFace(canvas, centerX, centerY, radius, accent)
    }

    private fun drawStaticFace(
        canvas: Canvas,
        cx: Float,
        cy: Float,
        radius: Float,
        accent: Int
    ) {
        val eyeY = cy - radius * 0.10f
        val eyeGap = radius * 0.40f
        val eyeRadius = radius * 0.045f

        fillPaint.color = withAlpha(accent, 225)
        canvas.drawCircle(cx - eyeGap, eyeY, eyeRadius, fillPaint)
        canvas.drawCircle(cx + eyeGap, eyeY, eyeRadius, fillPaint)

        val browY = eyeY - radius * 0.13f
        val browWidth = radius * 0.19f
        val browTilt = when (personality) {
            Personality.AGRESIVO -> -radius * 0.085f
            Personality.NEUTRO -> radius * 0.010f
            Personality.COMICO -> radius * 0.020f
            Personality.CONSPIRANOICO -> radius * 0.040f
        }

        drawBrow(canvas, cx - eyeGap, browY, browWidth, browTilt, accent)
        drawBrow(canvas, cx + eyeGap, browY, browWidth, -browTilt, accent)

        val mouthY = cy + radius * 0.30f
        val expression = when (personality) {
            Personality.AGRESIVO -> -0.30f
            Personality.NEUTRO -> 0f
            Personality.COMICO -> 0.38f
            Personality.CONSPIRANOICO -> -0.12f
        }

        drawMouth(canvas, cx, mouthY, radius * 0.25f, radius * 0.07f, expression, accent)
    }

    private fun drawBrow(
        canvas: Canvas,
        x: Float,
        y: Float,
        width: Float,
        tilt: Float,
        accent: Int
    ) {
        strokePaint.color = withAlpha(accent, 165)
        strokePaint.strokeWidth = dp(2f)

        val path = Path()
        path.moveTo(x - width, y + tilt)
        path.quadTo(x, y - dp(1.5f), x + width, y - tilt)
        canvas.drawPath(path, strokePaint)
    }

    private fun drawMouth(
        canvas: Canvas,
        cx: Float,
        cy: Float,
        width: Float,
        height: Float,
        expression: Float,
        accent: Int
    ) {
        strokePaint.color = withAlpha(accent, 195)
        strokePaint.strokeWidth = dp(1.6f)

        val smileDepth = expression.coerceIn(-0.5f, 0.6f) * height * 2.2f
        val path = Path()
        path.moveTo(cx - width, cy)
        path.quadTo(cx, cy + smileDepth, cx + width, cy)
        canvas.drawPath(path, strokePaint)
    }

    private fun withAlpha(color: Int, alpha: Int): Int =
        (color and 0x00FFFFFF) or ((alpha.coerceIn(0, 255)) shl 24)

    private fun dp(value: Float): Float =
        value * resources.displayMetrics.density
}
