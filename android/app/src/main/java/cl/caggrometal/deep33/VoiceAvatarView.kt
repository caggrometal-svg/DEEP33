package cl.caggrometal.deep33

import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.Path
import android.graphics.RectF
import android.os.Handler
import android.os.Looper
import android.util.AttributeSet
import android.view.View
import kotlin.math.min
import kotlin.math.sin

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
    private val animationHandler = Handler(Looper.getMainLooper())
    private var speakingAnimationRunning = false
    private var speakingPhase = 0f
    private var mouthOpen = 0f
    private val speakingAnimation = object : Runnable {
        override fun run() {
            if (state != AvatarState.SPEAKING) {
                speakingAnimationRunning = false
                mouthOpen = 0f
                invalidate()
                return
            }
            speakingPhase += 0.62f
            mouthOpen = ((sin(speakingPhase.toDouble()) + 1.0) * 0.5).toFloat()
            invalidate()
            animationHandler.postDelayed(this, 90L)
        }
    }

    fun setPersonality(value: Personality) {
        personality = value
        invalidate()
    }

    fun setVoiceState(value: AvatarState) {
        state = value
        if (value != AvatarState.LISTENING) {
            audioLevel = 0f
        }
        if (value == AvatarState.SPEAKING) {
            if (!speakingAnimationRunning) {
                speakingAnimationRunning = true
                speakingPhase = 0f
                animationHandler.removeCallbacks(speakingAnimation)
                animationHandler.post(speakingAnimation)
            }
        } else {
            speakingAnimationRunning = false
            speakingPhase = 0f
            mouthOpen = 0f
            animationHandler.removeCallbacks(speakingAnimation)
        }
        invalidate()
    }

    override fun onDetachedFromWindow() {
        speakingAnimationRunning = false
        animationHandler.removeCallbacks(speakingAnimation)
        super.onDetachedFromWindow()
    }

    fun setAudioLevel(value: Float) {
        // Show only the microphone input level. No facial animation is tied to audio.
        val target = value.coerceIn(0f, 1f)
        audioLevel += (target - audioLevel) * 0.35f
        if (kotlin.math.abs(audioLevel - target) < 0.01f) {
            audioLevel = target
        }
        invalidate()
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)

        val size = min(width, height).toFloat()
        val centerX = width / 2f
        val centerY = height / 2f
        val radius = size * 0.29f
        val accent = personality.accent

        // One static round face, one contour, two eyes, brows and mouth.
        // Only the mouth opens/closes while DEEP33 is speaking.
        // No pulse, orbit, wobble, blink, scale or audio-reactive facial effect.
        fillPaint.color = 0xFF0A0A0A.toInt()
        canvas.drawCircle(centerX, centerY, radius + dp(2f), fillPaint)

        strokePaint.color = withAlpha(accent, 205)
        strokePaint.strokeWidth = dp(2f)
        canvas.drawCircle(centerX, centerY, radius + dp(2f), strokePaint)

        drawFace(canvas, centerX, centerY, radius, accent)
        drawInputMeter(canvas, centerX, centerY, radius, accent)
    }

    private fun drawInputMeter(
        canvas: Canvas,
        cx: Float,
        cy: Float,
        radius: Float,
        accent: Int
    ) {
        val active = state == AvatarState.LISTENING
        val barCount = 9
        val barWidth = dp(3f)
        val gap = dp(5f)
        val maxHeight = dp(18f)
        val totalWidth = barCount * barWidth + (barCount - 1) * gap
        val startX = cx - totalWidth / 2f
        val baseY = cy + radius + dp(22f)
        val sensitivities = floatArrayOf(0.45f, 0.65f, 0.82f, 1.0f, 1.15f, 1.0f, 0.82f, 0.65f, 0.45f)

        for (index in 0 until barCount) {
            val x = startX + index * (barWidth + gap)
            val level = if (active) (audioLevel * sensitivities[index]).coerceIn(0f, 1f) else 0f
            val height = if (active) {
                (dp(3f) + maxHeight * level)
            } else {
                dp(2f)
            }
            val top = baseY - height
            fillPaint.color = if (active && level > 0.05f) {
                withAlpha(accent, 220)
            } else {
                withAlpha(accent, 70)
            }
            canvas.drawRoundRect(
                RectF(x, top, x + barWidth, baseY),
                barWidth,
                barWidth,
                fillPaint
            )
        }
    }

    private fun drawFace(
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

        drawMouth(canvas, cx, mouthY, radius * 0.25f, radius * 0.07f, expression, accent, mouthOpen)
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
        accent: Int,
        opening: Float
    ) {
        if (state == AvatarState.SPEAKING) {
            val openHeight = height * (1.0f + 4.8f * opening.coerceIn(0f, 1f))
            fillPaint.color = 0xFF030303.toInt()
            val mouthRect = RectF(
                cx - width,
                cy - openHeight / 2f,
                cx + width,
                cy + openHeight / 2f
            )
            canvas.drawRoundRect(
                mouthRect,
                openHeight / 2f,
                openHeight / 2f,
                fillPaint
            )
            strokePaint.color = withAlpha(accent, 205)
            strokePaint.strokeWidth = dp(1.6f)
            canvas.drawRoundRect(
                mouthRect,
                openHeight / 2f,
                openHeight / 2f,
                strokePaint
            )
            return
        }

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
