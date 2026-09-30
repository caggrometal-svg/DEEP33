package cl.caggrometal.deep33

import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.RectF
import android.util.AttributeSet
import android.view.View
import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.min
import kotlin.math.sin

enum class AvatarState { IDLE, LISTENING, THINKING, SPEAKING }

class VoiceAvatarView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null
) : View(context, attrs) {
    private val fillPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.FILL }
    private val strokePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE }
    private var personality = Personality.NEUTRO
    private var state = AvatarState.IDLE
    private var phase = 0f
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

        phase = (phase + personality.avatarMotion) % (2f * PI.toFloat())

        val size = min(width, height).toFloat()
        val centerX = width / 2f
        val centerY = height / 2f
        val radius = size * 0.29f
        val accent = personality.accent

        // A synthetic core replaces the old cartoon face. The personality is now
        // carried by the accent while the geometry stays mature and consistent.
        fillPaint.color = 0xFF050505.toInt()
        canvas.drawCircle(centerX, centerY, radius + dp(18f), fillPaint)

        val activity = when (state) {
            AvatarState.IDLE -> 0.08f
            AvatarState.LISTENING -> 0.24f + audioLevel * 0.64f
            AvatarState.THINKING -> 0.30f
            AvatarState.SPEAKING -> 0.48f + (sin(phase * 2f) * 0.24f + 0.24f)
        }

        // Ambient shell: keeps the avatar visually anchored to DEEP33's dark/red system
        // without turning it into a cartoon face.
        strokePaint.strokeCap = Paint.Cap.ROUND
        strokePaint.strokeWidth = dp(1.1f)
        strokePaint.color = withAlpha(accent, 18 + (activity * 24).toInt())
        canvas.drawCircle(centerX, centerY, radius + dp(31f) + dp(8f) * activity, strokePaint)
        strokePaint.color = withAlpha(accent, 10 + (activity * 18).toInt())
        canvas.drawCircle(centerX, centerY, radius + dp(42f) + dp(10f) * activity, strokePaint)

        if (state == AvatarState.LISTENING || state == AvatarState.SPEAKING) {
            strokePaint.color = withAlpha(accent, 42 + (activity * 55).toInt())
            strokePaint.strokeWidth = dp(2.0f)
            val activeRect = RectF(
                centerX - radius - dp(19f),
                centerY - radius - dp(19f),
                centerX + radius + dp(19f),
                centerY + radius + dp(19f)
            )
            canvas.drawArc(activeRect, -90f + phase * 24f, 110f + 90f * activity, false, strokePaint)
        }

        when (personality.avatarGeometry) {
            "ANGULAR" -> drawAggressive(canvas, centerX, centerY, radius, accent, activity)
            "ORBITAL" -> drawNeutral(canvas, centerX, centerY, radius, accent, activity)
            "WOBBLE" -> drawComic(canvas, centerX, centerY, radius, accent, activity)
            "CROSSHAIR" -> drawConspiranoic(canvas, centerX, centerY, radius, accent, activity)
        }

        val coreRadius = radius * (0.11f + 0.07f * activity)
        fillPaint.color = withAlpha(accent, 235)
        canvas.drawCircle(centerX, centerY, coreRadius, fillPaint)

        fillPaint.color = 0xFFF0F0F0.toInt()
        canvas.drawCircle(centerX, centerY, radius * 0.022f, fillPaint)

        if (state != AvatarState.IDLE) {
            val count = when (personality.avatarGeometry) {
                "ANGULAR" -> 2
                "CROSSHAIR" -> 4
                else -> 3
            }
            repeat(count) { index ->
                val angle = phase * 1.5f + index * (2f * PI.toFloat() / count.toFloat())
                val offset = if (personality == Personality.COMICO && index == 0) dp(6f) else 0f
                val dotRadius = radius + dp(24f) + dp(5f) * activity + offset
                val dotX = centerX + cos(angle) * dotRadius
                val dotY = centerY + sin(angle) * dotRadius
                fillPaint.color = withAlpha(accent, (100 + index * 35).coerceAtMost(230))
                canvas.drawCircle(dotX, dotY, dp(1.6f + activity * 1.2f), fillPaint)
            }
        }

        postInvalidateDelayed(33L)
    }

    private fun drawNeutral(canvas: Canvas, cx: Float, cy: Float, radius: Float, accent: Int, activity: Float) {
        for (ring in 1..3) {
            strokePaint.color = withAlpha(accent, 22 + ring * 12)
            strokePaint.strokeWidth = dp(1.2f)
            canvas.drawCircle(cx, cy, radius + dp(ring * 7f) + dp(10f) * activity, strokePaint)
        }
        drawArcs(canvas, cx, cy, radius, accent, activity, 66f, 150f)
    }

    private fun drawAggressive(canvas: Canvas, cx: Float, cy: Float, radius: Float, accent: Int, activity: Float) {
        strokePaint.strokeWidth = dp(1.4f)
        for (ring in 1..3) {
            strokePaint.color = withAlpha(accent, 28 + ring * 14)
            val inset = dp(ring * 7f) + dp(7f) * activity
            val rect = RectF(cx - radius - inset, cy - radius * 0.82f, cx + radius + inset, cy + radius * 0.82f)
            canvas.drawRoundRect(rect, dp(10f), dp(10f), strokePaint)
        }
        drawArcs(canvas, cx, cy, radius, accent, activity, 48f, 122f)
        strokePaint.color = withAlpha(accent, 185)
        strokePaint.strokeWidth = dp(2.1f)
        canvas.drawLine(cx - radius * 0.58f, cy, cx + radius * 0.58f, cy, strokePaint)
    }

    private fun drawComic(canvas: Canvas, cx: Float, cy: Float, radius: Float, accent: Int, activity: Float) {
        for (ring in 1..3) {
            strokePaint.color = withAlpha(accent, 22 + ring * 12)
            strokePaint.strokeWidth = dp(1.2f)
            val wobble = sin(phase * 1.4f + ring) * dp(3f) * activity
            val rect = RectF(cx - radius - dp(ring * 7f), cy - radius * 0.90f - wobble, cx + radius + dp(ring * 7f), cy + radius * 0.90f + wobble)
            canvas.drawOval(rect, strokePaint)
        }
        drawArcs(canvas, cx, cy, radius, accent, activity, 78f, 175f)
    }

    private fun drawConspiranoic(canvas: Canvas, cx: Float, cy: Float, radius: Float, accent: Int, activity: Float) {
        strokePaint.color = withAlpha(accent, 34)
        strokePaint.strokeWidth = dp(1.1f)
        for (ring in 1..3) {
            val offset = dp(ring * 6f) + dp(8f) * activity
            canvas.drawCircle(cx, cy, radius + offset, strokePaint)
        }
        drawArcs(canvas, cx, cy, radius, accent, activity, 34f, 210f)
        strokePaint.color = withAlpha(accent, 145)
        strokePaint.strokeWidth = dp(1.1f)
        canvas.drawLine(cx - radius * 0.92f, cy, cx + radius * 0.92f, cy, strokePaint)
        canvas.drawLine(cx, cy - radius * 0.92f, cx, cy + radius * 0.92f, strokePaint)
    }

    private fun drawArcs(canvas: Canvas, cx: Float, cy: Float, radius: Float, accent: Int, activity: Float, baseSweep: Float, multiplier: Float) {
        val arcRect = RectF(cx - radius - dp(7f), cy - radius - dp(7f), cx + radius + dp(7f), cy + radius + dp(7f))
        strokePaint.color = withAlpha(accent, 210)
        strokePaint.strokeWidth = dp(2.4f)
        val sweep = baseSweep + multiplier * activity
        canvas.drawArc(arcRect, -90f + phase * 57f, sweep, false, strokePaint)
        canvas.drawArc(arcRect, 90f + phase * 57f, sweep * 0.55f, false, strokePaint)
    }

    private fun withAlpha(color: Int, alpha: Int): Int =
        (color and 0x00FFFFFF) or ((alpha.coerceIn(0, 255)) shl 24)

    private fun dp(value: Float): Float =
        value * resources.displayMetrics.density
}
