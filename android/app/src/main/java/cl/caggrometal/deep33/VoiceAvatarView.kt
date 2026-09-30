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
        phase = (phase + 0.07f) % (2f * PI.toFloat())

        val width = width.toFloat()
        val height = height.toFloat()
        val centerX = width / 2f
        val centerY = height / 2f
        val radius = min(width, height) * 0.30f
        val accent = personality.accent

        fillPaint.color = 0xFF07070A.toInt()
        canvas.drawCircle(centerX, centerY, radius, fillPaint)

        // Subtle dark halo keeps the avatar readable without turning the UI neon.
        for (ring in 3 downTo 1) {
            strokePaint.color = withAlpha(accent, 18 + ring * 12)
            strokePaint.strokeWidth = dp((ring * 2).toFloat())
            canvas.drawCircle(
                centerX,
                centerY,
                radius + dp((10 + ring * 6).toFloat()),
                strokePaint
            )
        }

        val activity = when (state) {
            AvatarState.IDLE -> 0.12f
            AvatarState.LISTENING -> 0.35f + audioLevel * 0.55f
            AvatarState.THINKING -> 0.30f
            AvatarState.SPEAKING -> 0.50f + (sin(phase * 2f) * 0.25f + 0.25f)
        }

        strokePaint.color = accent
        strokePaint.strokeWidth = 3.5f
        canvas.drawCircle(
            centerX,
            centerY,
            radius + dp(7) + dp(5) * activity,
            strokePaint
        )

        val eyeY = centerY - radius * 0.16f
        val eyeOffset = radius * 0.36f
        fillPaint.color = 0xFFFFFFFF.toInt()
        canvas.drawCircle(centerX - eyeOffset, eyeY, radius * 0.05f, fillPaint)
        canvas.drawCircle(centerX + eyeOffset, eyeY, radius * 0.05f, fillPaint)

        val mouthTop = centerY + radius * 0.22f
        val mouthWidth = radius * (0.46f + 0.22f * activity)
        val mouthHeight = radius * (0.05f + 0.26f * activity)
        strokePaint.color = accent
        strokePaint.strokeWidth = 4f
        canvas.drawRoundRect(
            RectF(
                centerX - mouthWidth,
                mouthTop,
                centerX + mouthWidth,
                mouthTop + mouthHeight
            ),
            mouthHeight,
            mouthHeight,
            strokePaint
        )

        if (state == AvatarState.THINKING) {
            for (index in 0..2) {
                val angle = phase + index * (2f * PI.toFloat() / 3f)
                val dotX = centerX + cos(angle) * radius * 0.72f
                val dotY = centerY + sin(angle) * radius * 0.72f
                fillPaint.color = accent
                canvas.drawCircle(dotX, dotY, radius * 0.035f, fillPaint)
            }
        }

        postInvalidateDelayed(33L)
    }

    private fun withAlpha(color: Int, alpha: Int): Int =
        (color and 0x00FFFFFF) or ((alpha.coerceIn(0, 255)) shl 24)

    private fun dp(value: Float): Float =
        value * resources.displayMetrics.density
}
