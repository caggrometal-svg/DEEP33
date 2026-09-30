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

        phase = (phase + 0.035f) % (2f * PI.toFloat())

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
            AvatarState.LISTENING -> 0.28f + audioLevel * 0.60f
            AvatarState.THINKING -> 0.34f
            AvatarState.SPEAKING -> 0.50f + (sin(phase * 2f) * 0.24f + 0.24f)
        }

        strokePaint.strokeCap = Paint.Cap.ROUND

        for (ring in 1..3) {
            strokePaint.color = withAlpha(accent, 22 + ring * 12)
            strokePaint.strokeWidth = dp(1.2f)
            canvas.drawCircle(
                centerX,
                centerY,
                radius + dp((ring * 7).toFloat()) + dp(10f) * activity,
                strokePaint
            )
        }

        val arcRect = RectF(
            centerX - radius - dp(7f),
            centerY - radius - dp(7f),
            centerX + radius + dp(7f),
            centerY + radius + dp(7f)
        )
        strokePaint.color = withAlpha(accent, 210)
        strokePaint.strokeWidth = dp(2.4f)
        val sweep = 66f + 150f * activity
        canvas.drawArc(arcRect, -90f + phase * 57f, sweep, false, strokePaint)
        canvas.drawArc(arcRect, 90f + phase * 57f, sweep * 0.55f, false, strokePaint)

        val coreRadius = radius * (0.11f + 0.07f * activity)
        fillPaint.color = withAlpha(accent, 235)
        canvas.drawCircle(centerX, centerY, coreRadius, fillPaint)

        fillPaint.color = 0xFFF0F0F0.toInt()
        canvas.drawCircle(centerX, centerY, radius * 0.022f, fillPaint)

        if (state != AvatarState.IDLE) {
            for (index in 0..2) {
                val angle = phase * 1.5f + index * (2f * PI.toFloat() / 3f)
                val dotRadius = radius + dp(24f) + dp(5f) * activity
                val dotX = centerX + cos(angle) * dotRadius
                val dotY = centerY + sin(angle) * dotRadius
                fillPaint.color = withAlpha(accent, 100 + index * 35)
                canvas.drawCircle(dotX, dotY, dp(1.6f + activity * 1.2f), fillPaint)
            }
        }

        postInvalidateDelayed(33L)
    }

    private fun withAlpha(color: Int, alpha: Int): Int =
        (color and 0x00FFFFFF) or ((alpha.coerceIn(0, 255)) shl 24)

    private fun dp(value: Float): Float =
        value * resources.displayMetrics.density
}
