package cl.caggrometal.deep33

import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.Path
import android.util.AttributeSet
import android.view.View
import kotlin.math.abs
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
    private var audioLevel = 0f

    fun setPersonality(value: Personality) {
        if (personality == value) return
        personality = value
        invalidate()
    }

    fun setVoiceState(value: AvatarState) {
        if (state == value) return
        state = value
        invalidate()
    }

    fun setAudioLevel(value: Float) {
        // Quantize RMS updates so a noisy recognizer callback stream does not cause
        // unnecessary redraws. The avatar stays lightweight by design.
        val next = value.coerceIn(0f, 1f)
        if (abs(next - audioLevel) < 0.05f) return
        audioLevel = next
        invalidate()
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)

        val size = min(width, height).toFloat()
        val centerX = width / 2f
        val centerY = height / 2f
        val radius = size * 0.36f
        val accent = personality.accent

        // Minimal geometry: one face, one optional state ring, two eyes, brows and mouth.
        // There is deliberately no nose and no expensive blur/shadow layer.
        val ringExpansion = when (state) {
            AvatarState.IDLE -> 0f
            AvatarState.LISTENING -> dp(4f)
            AvatarState.THINKING -> dp(6f)
            AvatarState.SPEAKING -> dp(4f) + dp(7f) * audioLevel
        }

        fillPaint.color = 0xFF080808.toInt()
        canvas.drawCircle(centerX, centerY, radius, fillPaint)

        strokePaint.strokeWidth = dp(2f)
        strokePaint.color = withAlpha(accent, when (state) {
            AvatarState.IDLE -> 195
            AvatarState.LISTENING -> 230
            AvatarState.THINKING -> 205
            AvatarState.SPEAKING -> 245
        })
        canvas.drawCircle(centerX, centerY, radius, strokePaint)

        if (state != AvatarState.IDLE) {
            strokePaint.strokeWidth = dp(1f)
            strokePaint.color = withAlpha(accent, 80)
            canvas.drawCircle(centerX, centerY, radius + ringExpansion, strokePaint)
        }

        drawFace(canvas, centerX, centerY, radius, accent)
    }

    private fun drawFace(canvas: Canvas, cx: Float, cy: Float, radius: Float, accent: Int) {
        val eyeY = cy - radius * 0.11f
        val eyeGap = radius * 0.39f
        val eyeRadius = radius * 0.048f

        fillPaint.color = withAlpha(accent, if (state == AvatarState.LISTENING) 255 else 225)
        canvas.drawCircle(cx - eyeGap, eyeY, eyeRadius, fillPaint)
        canvas.drawCircle(cx + eyeGap, eyeY, eyeRadius, fillPaint)

        val browY = eyeY - radius * 0.15f
        val browWidth = radius * 0.21f
        val browTilt = when (personality) {
            Personality.AGRESIVO -> -radius * 0.085f
            Personality.NEUTRO -> radius * 0.010f
            Personality.COMICO -> radius * 0.020f
            Personality.CONSPIRANOICO -> radius * 0.040f
        }
        drawBrow(canvas, cx - eyeGap, browY, browWidth, browTilt, accent)
        drawBrow(canvas, cx + eyeGap, browY, browWidth, -browTilt, accent)

        val mouthY = cy + radius * 0.30f
        val baseExpression = when (personality) {
            Personality.AGRESIVO -> -0.30f
            Personality.NEUTRO -> 0f
            Personality.COMICO -> 0.38f
            Personality.CONSPIRANOICO -> -0.12f
        }
        val speakingLift = if (state == AvatarState.SPEAKING) audioLevel * 0.18f else 0f
        drawMouth(
            canvas,
            cx,
            mouthY,
            radius * 0.25f,
            radius * (0.075f + speakingLift),
            baseExpression,
            accent
        )
    }

    private fun drawBrow(
        canvas: Canvas,
        x: Float,
        y: Float,
        width: Float,
        tilt: Float,
        accent: Int
    ) {
        strokePaint.color = withAlpha(accent, 170)
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
        strokePaint.color = withAlpha(accent, 205)
        strokePaint.strokeWidth = dp(1.7f)
        val smileDepth = expression.coerceIn(-0.5f, 0.6f) * height * 2.0f
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