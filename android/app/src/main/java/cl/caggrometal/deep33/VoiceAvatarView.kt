package cl.caggrometal.deep33

import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.Path
import android.graphics.RectF
import android.util.AttributeSet
import android.view.View
import kotlin.math.PI
import kotlin.math.abs
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
    private var gestureTime = 0f

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

        drawFaceGestures(canvas, centerX, centerY, radius, accent, activity, gestureTime)

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

    private fun drawFaceGestures(
        canvas: Canvas,
        cx: Float,
        cy: Float,
        radius: Float,
        accent: Int,
        activity: Float,
        time: Float,
    ) {
        val breath = sin(time * 1.25f) * 0.018f
        val listening = if (state == AvatarState.LISTENING) 1f else 0f
        val thinking = if (state == AvatarState.THINKING) 1f else 0f
        val speaking = if (state == AvatarState.SPEAKING) 1f else 0f

        val gazeX = sin(time * if (listening > 0f) 0.72f else 0.46f) * radius * 0.045f
        val gazeY = sin(time * 0.31f + 1.1f) * radius * 0.018f - thinking * radius * 0.018f

        val blinkWave = abs(sin(time * 0.43f + sin(time * 0.071f) * 0.9f))
        val blink = if (blinkWave > 0.975f) ((blinkWave - 0.975f) / 0.025f).coerceIn(0f, 1f) else 0f
        val eyeOpen = 1f - blink

        val eyeY = cy - radius * (0.10f + breath)
        val eyeGap = radius * 0.40f
        val eyeW = radius * 0.19f
        val eyeH = radius * (0.075f + 0.09f * eyeOpen)
        val irisR = radius * 0.040f

        val browLift = when (personality) {
            Personality.AGRESIVO -> -radius * 0.030f
            Personality.NEUTRO -> radius * 0.004f
            Personality.COMICO -> -radius * 0.012f
            Personality.CONSPIRANOICO -> -radius * 0.020f
        }
        val browTilt = when (personality) {
            Personality.AGRESIVO -> radius * 0.065f
            Personality.NEUTRO -> radius * 0.010f
            Personality.COMICO -> radius * 0.030f * sin(time * 1.2f)
            Personality.CONSPIRANOICO -> radius * 0.040f
        }

        drawBrow(canvas, cx - eyeGap, eyeY - radius * 0.13f + browLift, eyeW, browTilt, accent)
        drawBrow(canvas, cx + eyeGap, eyeY - radius * 0.13f + browLift, eyeW, -browTilt, accent)
        drawEye(canvas, cx - eyeGap, eyeY, eyeW, eyeH, irisR, gazeX, gazeY, eyeOpen, accent)
        drawEye(canvas, cx + eyeGap, eyeY, eyeW, eyeH, irisR, gazeX, gazeY, eyeOpen, accent)

        val mouthY = cy + radius * (0.30f + breath)
        val expression = when (personality) {
            Personality.AGRESIVO -> -0.30f
            Personality.NEUTRO -> 0.02f
            Personality.COMICO -> 0.44f + 0.08f * sin(time * 1.1f)
            Personality.CONSPIRANOICO -> -0.12f
        }
        val speakingPulse = if (speaking > 0f) (0.5f + 0.5f * sin(time * 8.5f)).coerceIn(0f, 1f) else 0f
        val listeningCue = if (listening > 0f) 0.12f * sin(time * 1.6f) else 0f

        drawMouth(
            canvas,
            cx,
            mouthY,
            radius * 0.25f,
            radius * (0.035f + 0.050f * speakingPulse + 0.020f * abs(expression)),
            expression + listeningCue,
            speakingPulse,
            accent
        )

        val tilt = when (personality) {
            Personality.AGRESIVO -> sin(time * 0.50f) * 0.010f
            Personality.NEUTRO -> sin(time * 0.37f) * 0.006f
            Personality.COMICO -> sin(time * 0.92f) * 0.020f
            Personality.CONSPIRANOICO -> sin(time * 0.41f) * 0.014f
        }
        if (tilt != 0f) {
            canvas.save()
            canvas.rotate(tilt * 57.29578f, cx, cy)
            strokePaint.color = withAlpha(accent, 95)
            strokePaint.strokeWidth = dp(1.0f)
            canvas.drawLine(cx - radius * 0.035f, cy + radius * 0.05f, cx + radius * 0.035f, cy + radius * 0.05f, strokePaint)
            canvas.restore()
        }
    }

    private fun drawEye(
        canvas: Canvas,
        x: Float,
        y: Float,
        width: Float,
        height: Float,
        irisRadius: Float,
        gazeX: Float,
        gazeY: Float,
        openness: Float,
        accent: Int,
    ) {
        strokePaint.strokeWidth = dp(1.5f)
        strokePaint.color = withAlpha(accent, 190)
        val eyeRect = RectF(x - width, y - height, x + width, y + height)
        if (openness < 0.16f) {
            canvas.drawLine(x - width * 0.85f, y, x + width * 0.85f, y + dp(0.5f), strokePaint)
            return
        }
        canvas.drawOval(eyeRect, strokePaint)
        fillPaint.color = withAlpha(accent, 225)
        val px = x + gazeX.coerceIn(-width * 0.32f, width * 0.32f)
        val py = y + gazeY.coerceIn(-height * 0.20f, height * 0.20f)
        canvas.drawCircle(px, py, irisRadius * (0.8f + 0.2f * openness), fillPaint)
        fillPaint.color = 0xFFEFEFEF.toInt()
        canvas.drawCircle(px - irisRadius * 0.22f, py - irisRadius * 0.22f, irisRadius * 0.18f, fillPaint)
    }

    private fun drawBrow(canvas: Canvas, x: Float, y: Float, width: Float, tilt: Float, accent: Int) {
        strokePaint.color = withAlpha(accent, 170)
        strokePaint.strokeWidth = dp(2.2f)
        val path = Path()
        path.moveTo(x - width, y + tilt)
        path.quadTo(x, y - dp(2.0f), x + width, y - tilt)
        canvas.drawPath(path, strokePaint)
    }

    private fun drawMouth(
        canvas: Canvas,
        cx: Float,
        cy: Float,
        width: Float,
        height: Float,
        expression: Float,
        speakingPulse: Float,
        accent: Int,
    ) {
        strokePaint.color = withAlpha(accent, 205)
        strokePaint.strokeWidth = dp(1.7f)
        if (speakingPulse > 0.12f) {
            val oval = RectF(cx - width, cy - height, cx + width, cy + height)
            canvas.drawOval(oval, strokePaint)
            return
        }
        val smileDepth = expression.coerceIn(-0.5f, 0.6f) * height * 2.4f
        val path = Path()
        path.moveTo(cx - width, cy)
        path.quadTo(cx, cy + smileDepth, cx + width, cy)
        canvas.drawPath(path, strokePaint)
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
