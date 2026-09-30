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

        val phaseSpeed = when (personality) {
            Personality.AGRESIVO -> 0.060f
            Personality.NEUTRO -> 0.030f
            Personality.COMICO -> 0.045f
            Personality.CONSPIRANOICO -> 0.022f
        }
        phase = (phase + phaseSpeed) % (2f * PI.toFloat())

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
        val sweep = when (personality) {
            Personality.AGRESIVO -> 48f + 118f * activity
            Personality.NEUTRO -> 66f + 150f * activity
            Personality.COMICO -> 78f + 136f * activity
            Personality.CONSPIRANOICO -> 54f + 128f * activity
        }
        val phaseDegrees = phase * when (personality) {
            Personality.AGRESIVO -> 82f
            Personality.NEUTRO -> 57f
            Personality.COMICO -> 67f
            Personality.CONSPIRANOICO -> 41f
        }
        canvas.drawArc(arcRect, -90f + phaseDegrees, sweep, false, strokePaint)
        canvas.drawArc(
            arcRect,
            90f + phaseDegrees + if (personality == Personality.CONSPIRANOICO) 37f else 0f,
            sweep * if (personality == Personality.AGRESIVO) 0.42f else 0.55f,
            false,
            strokePaint
        )

        val coreRadius = radius * (0.11f + 0.07f * activity)
        fillPaint.color = withAlpha(accent, 235)
        canvas.drawCircle(centerX, centerY, coreRadius, fillPaint)

        fillPaint.color = 0xFFF0F0F0.toInt()
        canvas.drawCircle(centerX, centerY, radius * 0.022f, fillPaint)

        if (state != AvatarState.IDLE) {
            for (index in 0..2) {
                val orbit = when (personality) {
                    Personality.AGRESIVO -> radius + dp(27f)
                    Personality.NEUTRO -> radius + dp(24f)
                    Personality.COMICO -> radius + dp(22f + index * 3f)
                    Personality.CONSPIRANOICO -> radius + dp(30f - index * 2f)
                }
                val angle = phase * when (personality) {
                    Personality.AGRESIVO -> 1.9f
                    Personality.NEUTRO -> 1.5f
                    Personality.COMICO -> 1.15f
                    Personality.CONSPIRANOICO -> 0.85f
                } + index * (2f * PI.toFloat() / 3f)
                val dotX = centerX + cos(angle) * (orbit + dp(5f) * activity)
                val dotY = centerY + sin(angle) * (orbit + dp(5f) * activity)
                fillPaint.color = withAlpha(accent, 100 + index * 35)
                canvas.drawCircle(dotX, dotY, dp(1.6f + activity * 1.2f), fillPaint)
            }
        }

        if (personality == Personality.CONSPIRANOICO && state != AvatarState.IDLE) {
            strokePaint.color = withAlpha(accent, 150)
            strokePaint.strokeWidth = dp(1f)
            canvas.drawLine(centerX - dp(34f), centerY, centerX + dp(34f), centerY, strokePaint)
            canvas.drawLine(centerX, centerY - dp(34f), centerX, centerY + dp(34f), strokePaint)
        }

        postInvalidateDelayed(33L)
    }

    private fun withAlpha(color: Int, alpha: Int): Int =
        (color and 0x00FFFFFF) or ((alpha.coerceIn(0, 255)) shl 24)

    private fun dp(value: Float): Float =
        value * resources.displayMetrics.density
}
