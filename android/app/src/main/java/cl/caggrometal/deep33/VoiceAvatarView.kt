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
        if (state == AvatarState.LISTENING || state == AvatarState.SPEAKING) {
            invalidate()
        }
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)

        val size = min(width, height).toFloat()
        val centerX = width / 2f
        val centerY = height / 2f
        val radius = size * 0.29f
        val accent = personality.accent

        // Minimal visual system: one static personality contour, static face,
        // and one small active core. No rotating halos, particles or decorative rings.
        fillPaint.color = 0xFF050505.toInt()
        canvas.drawCircle(centerX, centerY, radius + dp(18f), fillPaint)

        drawPersonalityContour(canvas, centerX, centerY, radius, accent)
        drawStaticFace(canvas, centerX, centerY, radius, accent)

        val activeLevel = when (state) {
            AvatarState.LISTENING, AvatarState.SPEAKING -> audioLevel
            AvatarState.THINKING -> 0.16f
            AvatarState.IDLE -> 0f
        }

        val coreRadius = radius * (0.11f + 0.035f * activeLevel)
        fillPaint.color = withAlpha(accent, if (state == AvatarState.IDLE) 210 else 235)
        canvas.drawCircle(centerX, centerY, coreRadius, fillPaint)

        fillPaint.color = 0xFFF0F0F0.toInt()
        canvas.drawCircle(centerX, centerY, radius * 0.022f, fillPaint)
    }

    private fun drawPersonalityContour(
        canvas: Canvas,
        cx: Float,
        cy: Float,
        radius: Float,
        accent: Int
    ) {
        strokePaint.color = withAlpha(accent, 190)
        strokePaint.strokeWidth = dp(2f)

        when (personality.avatarGeometry) {
            "ANGULAR" -> {
                val inset = dp(12f)
                val rect = RectF(
                    cx - radius - inset,
                    cy - radius * 0.82f,
                    cx + radius + inset,
                    cy + radius * 0.82f
                )
                canvas.drawRoundRect(rect, dp(10f), dp(10f), strokePaint)
            }

            "ORBITAL" -> {
                canvas.drawCircle(cx, cy, radius + dp(10f), strokePaint)
            }

            "WOBBLE" -> {
                val rect = RectF(
                    cx - radius - dp(10f),
                    cy - radius * 0.88f,
                    cx + radius + dp(10f),
                    cy + radius * 0.88f
                )
                canvas.drawOval(rect, strokePaint)
            }

            "CROSSHAIR" -> {
                canvas.drawCircle(cx, cy, radius + dp(9f), strokePaint)
                strokePaint.strokeWidth = dp(1.1f)
                canvas.drawLine(
                    cx - radius * 0.78f,
                    cy,
                    cx + radius * 0.78f,
                    cy,
                    strokePaint
                )
                canvas.drawLine(
                    cx,
                    cy - radius * 0.78f,
                    cx,
                    cy + radius * 0.78f,
                    strokePaint
                )
            }
        }
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

        // Static eyes: no movement or blink animation.
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

        drawBrow(
            canvas,
            cx - eyeGap,
            browY,
            browWidth,
            browTilt,
            accent
        )
        drawBrow(
            canvas,
            cx + eyeGap,
            browY,
            browWidth,
            -browTilt,
            accent
        )

        val mouthY = cy + radius * 0.30f
        val expression = when (personality) {
            Personality.AGRESIVO -> -0.30f
            Personality.NEUTRO -> 0f
            Personality.COMICO -> 0.38f
            Personality.CONSPIRANOICO -> -0.12f
        }

        drawMouth(
            canvas = canvas,
            cx = cx,
            cy = mouthY,
            width = radius * 0.25f,
            height = radius * 0.07f,
            expression = expression,
            speaking = state == AvatarState.SPEAKING,
            audioLevel = audioLevel,
            accent = accent
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
        speaking: Boolean,
        audioLevel: Float,
        accent: Int
    ) {
        strokePaint.color = withAlpha(accent, 195)
        strokePaint.strokeWidth = dp(1.6f)

        if (speaking) {
            val openHeight = height + audioLevel.coerceIn(0f, 1f) * dp(4f)
            canvas.drawOval(
                RectF(
                    cx - width,
                    cy - openHeight,
                    cx + width,
                    cy + openHeight
                ),
                strokePaint
            )
            return
        }

        val smileDepth = expression.coerceIn(-0.5f, 0.6f) * height * 2.2f
        val path = Path()
        path.moveTo(cx - width, cy)
        path.quadTo(cx, cy + smileDepth, cx + width, cy)
        canvas.drawPath(path, strokePaint)
    }

    private fun radiusIndependent(value: Float): Float = value.coerceIn(0f, 1f)

    private fun withAlpha(color: Int, alpha: Int): Int =
        (color and 0x00FFFFFF) or ((alpha.coerceIn(0, 255)) shl 24)

    private fun dp(value: Float): Float =
        value * resources.displayMetrics.density
}
