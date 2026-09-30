package cl.caggrometal.deep33

import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.Path
import android.graphics.RectF
import android.util.AttributeSet
import android.view.View
import kotlin.math.abs
import kotlin.math.min
import kotlin.math.sin

enum class AvatarState { IDLE, LISTENING, THINKING, SPEAKING }

class VoiceAvatarView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null
) : View(context, attrs) {
    private val fillPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.FILL }
    private val strokePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeCap = Paint.Cap.ROUND
        strokeJoin = Paint.Join.ROUND
    }
    private var personality = Personality.NEUTRO
    private var state = AvatarState.IDLE
    private var gestureTime = 0f
    private var audioLevel = 0f

    fun setPersonality(value: Personality) {
        personality = value
        invalidate()
    }

    fun setVoiceState(value: AvatarState) {
        state = value
        if (value == AvatarState.IDLE) audioLevel = 0f
        invalidate()
    }

    fun setAudioLevel(value: Float) {
        audioLevel = value.coerceIn(0f, 1f)
        invalidate()
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)

        gestureTime = System.nanoTime() / 1_000_000_000f
        val size = min(width, height).toFloat()
        val cx = width / 2f
        val cy = height / 2f
        val radius = size * 0.31f
        val accent = personality.accent

        // Minimal face: one clean round shape, with only eyes, brows and mouth.
        fillPaint.color = 0xFF08080A.toInt()
        canvas.drawCircle(cx, cy, radius, fillPaint)

        strokePaint.strokeWidth = dp(1.4f)
        strokePaint.color = withAlpha(accent, 135)
        canvas.drawCircle(cx, cy, radius, strokePaint)

        val listening = if (state == AvatarState.LISTENING) 1f else 0f
        val thinking = if (state == AvatarState.THINKING) 1f else 0f
        val speaking = if (state == AvatarState.SPEAKING) 1f else 0f

        val gazeX = sin(gestureTime * if (listening > 0f) 0.70f else 0.42f) * radius * 0.035f
        val gazeY = sin(gestureTime * 0.28f) * radius * 0.012f - thinking * radius * 0.015f

        val blinkWave = abs(sin(gestureTime * 0.43f + sin(gestureTime * 0.071f) * 0.9f))
        val blink = if (blinkWave > 0.978f) ((blinkWave - 0.978f) / 0.022f).coerceIn(0f, 1f) else 0f
        val eyeOpen = 1f - blink

        val eyeY = cy - radius * 0.10f
        val eyeGap = radius * 0.37f
        val eyeW = radius * 0.17f
        val eyeH = radius * (0.055f + 0.075f * eyeOpen)
        val irisR = radius * 0.035f

        val browLift = when (personality) {
            Personality.AGRESIVO -> -radius * 0.025f
            Personality.NEUTRO -> 0f
            Personality.COMICO -> -radius * 0.010f
            Personality.CONSPIRANOICO -> -radius * 0.016f
        }
        val browTilt = when (personality) {
            Personality.AGRESIVO -> radius * 0.055f
            Personality.NEUTRO -> radius * 0.008f
            Personality.COMICO -> radius * 0.022f * sin(gestureTime * 1.1f)
            Personality.CONSPIRANOICO -> radius * 0.032f
        }

        drawBrow(canvas, cx - eyeGap, eyeY - radius * 0.14f + browLift, eyeW, browTilt, accent)
        drawBrow(canvas, cx + eyeGap, eyeY - radius * 0.14f + browLift, eyeW, -browTilt, accent)
        drawEye(canvas, cx - eyeGap, eyeY, eyeW, eyeH, irisR, gazeX, gazeY, eyeOpen, accent)
        drawEye(canvas, cx + eyeGap, eyeY, eyeW, eyeH, irisR, gazeX, gazeY, eyeOpen, accent)

        val expression = when (personality) {
            Personality.AGRESIVO -> -0.25f
            Personality.NEUTRO -> 0.02f
            Personality.COMICO -> 0.28f
            Personality.CONSPIRANOICO -> -0.08f
        }
        val mouthPulse = if (speaking > 0f) {
            (0.45f + 0.55f * sin(gestureTime * 7.5f)).coerceIn(0f, 1f)
        } else 0f
        drawMouth(canvas, cx, cy + radius * 0.30f, radius * 0.23f,
            radius * (0.030f + 0.035f * mouthPulse), expression, mouthPulse, accent)

        // No orbiting rings or rotating visual effects.
        postInvalidateDelayed(80L)
    }

    private fun drawEye(canvas: Canvas, x: Float, y: Float, width: Float, height: Float,
        irisRadius: Float, gazeX: Float, gazeY: Float, openness: Float, accent: Int) {
        strokePaint.strokeWidth = dp(1.4f)
        strokePaint.color = withAlpha(accent, 205)
        if (openness < 0.16f) {
            canvas.drawLine(x - width * 0.8f, y, x + width * 0.8f, y, strokePaint)
            return
        }
        canvas.drawOval(RectF(x - width, y - height, x + width, y + height), strokePaint)
        fillPaint.color = withAlpha(accent, 230)
        val px = x + gazeX.coerceIn(-width * 0.28f, width * 0.28f)
        val py = y + gazeY.coerceIn(-height * 0.20f, height * 0.20f)
        canvas.drawCircle(px, py, irisRadius, fillPaint)
    }

    private fun drawBrow(canvas: Canvas, x: Float, y: Float, width: Float, tilt: Float, accent: Int) {
        strokePaint.color = withAlpha(accent, 180)
        strokePaint.strokeWidth = dp(2.0f)
        val path = Path()
        path.moveTo(x - width, y + tilt)
        path.quadTo(x, y - dp(1.5f), x + width, y - tilt)
        canvas.drawPath(path, strokePaint)
    }

    private fun drawMouth(canvas: Canvas, cx: Float, cy: Float, width: Float, height: Float,
        expression: Float, speakingPulse: Float, accent: Int) {
        strokePaint.color = withAlpha(accent, 215)
        strokePaint.strokeWidth = dp(1.7f)
        if (speakingPulse > 0.14f) {
            canvas.drawOval(RectF(cx - width, cy - height, cx + width, cy + height), strokePaint)
            return
        }
        val smileDepth = expression.coerceIn(-0.4f, 0.45f) * height * 2.3f
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