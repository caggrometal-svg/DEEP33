package cl.caggrometal.deep33

import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.Path
import android.graphics.RectF
import android.util.AttributeSet
import android.view.View
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
    private var audioLevel = 0f
    private var animationRunning = false

    fun setPersonality(value: Personality) {
        personality = value
        invalidate()
    }

    fun setVoiceState(value: AvatarState) {
        state = value
        if (value == AvatarState.IDLE) audioLevel = 0f
        startAnimationIfNeeded()
        invalidate()
    }

    fun setAudioLevel(value: Float) {
        audioLevel = value.coerceIn(0f, 1f)
        invalidate()
    }

    override fun onAttachedToWindow() {
        super.onAttachedToWindow()
        startAnimationIfNeeded()
    }

    override fun onDetachedFromWindow() {
        animationRunning = false
        super.onDetachedFromWindow()
    }

    private fun startAnimationIfNeeded() {
        if (animationRunning || !isAttachedToWindow) return
        animationRunning = true
        post(animationTick)
    }

    private val animationTick = object : Runnable {
        override fun run() {
            if (!isAttachedToWindow) {
                animationRunning = false
                return
            }
            invalidate()
            postDelayed(this, if (state == AvatarState.IDLE) 180L else 70L)
        }
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)

        val now = System.currentTimeMillis()
        val size = min(width, height).toFloat()
        val cx = width / 2f
        val cy = height / 2f
        val radius = size * 0.31f
        val accent = personality.accent

        // Static, lightweight face: no nose, no head rings, no orbiting effects,
        // and no continuous redraw loop. State changes redraw only when needed.
        fillPaint.color = 0xFF08080A.toInt()
        canvas.drawCircle(cx, cy, radius, fillPaint)

        strokePaint.strokeWidth = dp(1.4f)
        strokePaint.color = withAlpha(accent, 160)
        canvas.drawCircle(cx, cy, radius, strokePaint)

        val headMotion = when (state) {
            AvatarState.SPEAKING -> sin(now / 520.0).toFloat() * 0.035f
            AvatarState.LISTENING -> sin(now / 900.0).toFloat() * 0.018f
            AvatarState.THINKING -> sin(now / 1250.0).toFloat() * 0.012f
            AvatarState.IDLE -> 0f
        }
        canvas.save()
        canvas.rotate(headMotion * 180f / Math.PI.toFloat(), cx, cy)

        val eyeY = cy - radius * 0.10f
        val eyeGap = radius * 0.37f
        val eyeW = radius * 0.17f
        val eyeH = radius * 0.12f
        val irisR = radius * 0.035f

        val blinkPhase = (now % 4200L).toFloat()
        val blink = when {
            blinkPhase < 120f -> 0.08f
            blinkPhase < 180f -> 0.45f
            blinkPhase < 240f -> 1f
            else -> 0f
        }
        val eyeOpen = 1f - blink
        val gazeX = when (state) {
            AvatarState.THINKING -> sin(now / 650.0).toFloat() * radius * 0.035f
            AvatarState.LISTENING -> sin(now / 1100.0).toFloat() * radius * 0.02f
            AvatarState.SPEAKING -> sin(now / 780.0).toFloat() * radius * 0.015f
            AvatarState.IDLE -> 0f
        }
        val gazeY = if (state == AvatarState.THINKING) -radius * 0.018f else 0f

        val browY = eyeY - radius * 0.14f
        when (personality) {
            Personality.AGRESIVO -> {
                drawBrow(canvas, cx - eyeGap, browY, eyeW, -radius * 0.07f, radius * 0.07f, accent)
                drawBrow(canvas, cx + eyeGap, browY, eyeW, radius * 0.07f, -radius * 0.07f, accent)
            }
            Personality.NEUTRO -> {
                drawBrow(canvas, cx - eyeGap, browY, eyeW, 0f, 0f, accent)
                drawBrow(canvas, cx + eyeGap, browY, eyeW, 0f, 0f, accent)
            }
            Personality.COMICO -> {
                drawBrow(canvas, cx - eyeGap, browY, eyeW, radius * 0.05f, -radius * 0.02f, accent)
                drawBrow(canvas, cx + eyeGap, browY, eyeW, -radius * 0.02f, radius * 0.05f, accent)
            }
            Personality.CONSPIRANOICO -> {
                drawBrow(canvas, cx - eyeGap, browY, eyeW, radius * 0.08f, -radius * 0.01f, accent)
                drawBrow(canvas, cx + eyeGap, browY, eyeW, -radius * 0.04f, radius * 0.02f, accent)
            }
        }
        drawEye(canvas, cx - eyeGap + gazeX, eyeY + gazeY, eyeW, eyeH * eyeOpen, irisR, accent)
        drawEye(canvas, cx + eyeGap + gazeX, eyeY + gazeY, eyeW, eyeH * eyeOpen, irisR, accent)

        drawExpressionMouth(
            canvas,
            cx,
            cy + radius * 0.30f,
            radius * (0.23f + audioLevel * 0.06f),
            accent
        )
        canvas.restore()
    }

    private fun drawEye(
        canvas: Canvas,
        x: Float,
        y: Float,
        width: Float,
        height: Float,
        irisRadius: Float,
        accent: Int
    ) {
        strokePaint.strokeWidth = dp(1.4f)
        strokePaint.color = withAlpha(accent, 220)
        canvas.drawOval(RectF(x - width, y - height, x + width, y + height), strokePaint)
        fillPaint.color = withAlpha(accent, 235)
        canvas.drawCircle(x, y, irisRadius, fillPaint)
    }

    private fun drawBrow(
        canvas: Canvas,
        x: Float,
        y: Float,
        width: Float,
        startTilt: Float,
        endTilt: Float,
        accent: Int
    ) {
        strokePaint.color = withAlpha(accent, 205)
        strokePaint.strokeWidth = dp(2.2f)
        val path = Path()
        path.moveTo(x - width, y + startTilt)
        path.quadTo(x, y - dp(1.5f), x + width, y + endTilt)
        canvas.drawPath(path, strokePaint)
    }

    private fun drawExpressionMouth(
        canvas: Canvas,
        cx: Float,
        cy: Float,
        width: Float,
        accent: Int
    ) {
        strokePaint.color = withAlpha(accent, 225)
        strokePaint.strokeWidth = dp(1.8f)
        val path = Path()
        val speaking = state == AvatarState.SPEAKING
        when (personality) {
            Personality.AGRESIVO -> {
                path.moveTo(cx - width, cy - if (speaking) dp(1f) else 0f)
                path.quadTo(cx, cy + width * 0.20f, cx + width, cy - if (speaking) dp(1f) else 0f)
            }
            Personality.NEUTRO -> {
                if (speaking) {
                    canvas.drawOval(RectF(cx - width * 0.72f, cy - dp(2.2f), cx + width * 0.72f, cy + dp(2.2f)), strokePaint)
                    return
                }
                path.moveTo(cx - width, cy)
                path.lineTo(cx + width, cy)
            }
            Personality.COMICO -> {
                path.moveTo(cx - width, cy - dp(1f))
                path.quadTo(cx, cy + width * 0.24f, cx + width, cy - dp(1f))
            }
            Personality.CONSPIRANOICO -> {
                path.moveTo(cx - width, cy + dp(1.5f))
                path.quadTo(cx, cy - width * 0.08f, cx + width, cy - dp(1.5f))
            }
        }
        canvas.drawPath(path, strokePaint)
    }

    private fun withAlpha(color: Int, alpha: Int): Int =
        (color and 0x00FFFFFF) or ((alpha.coerceIn(0, 255)) shl 24)

    private fun dp(value: Float): Float =
        value * resources.displayMetrics.density
}
