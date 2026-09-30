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
    private val fillPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.FILL }
    private val strokePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeCap = Paint.Cap.ROUND
        strokeJoin = Paint.Join.ROUND
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
        invalidate()
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)

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

        val eyeY = cy - radius * 0.10f
        val eyeGap = radius * 0.37f
        val eyeW = radius * 0.17f
        val eyeH = radius * 0.12f
        val irisR = radius * 0.035f

        drawBrow(canvas, cx - eyeGap, eyeY - radius * 0.14f, eyeW, 0f, accent)
        drawBrow(canvas, cx + eyeGap, eyeY - radius * 0.14f, eyeW, 0f, accent)
        drawEye(canvas, cx - eyeGap, eyeY, eyeW, eyeH, irisR, accent)
        drawEye(canvas, cx + eyeGap, eyeY, eyeW, eyeH, irisR, accent)

        val mouthHeight = if (state == AvatarState.SPEAKING) radius * 0.065f else radius * 0.030f
        drawMouth(canvas, cx, cy + radius * 0.30f, radius * 0.23f, mouthHeight, accent)
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

    private fun drawBrow(canvas: Canvas, x: Float, y: Float, width: Float, tilt: Float, accent: Int) {
        strokePaint.color = withAlpha(accent, 185)
        strokePaint.strokeWidth = dp(2.0f)
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
        accent: Int
    ) {
        strokePaint.color = withAlpha(accent, 225)
        strokePaint.strokeWidth = dp(1.7f)
        canvas.drawOval(RectF(cx - width, cy - height, cx + width, cy + height), strokePaint)
    }

    private fun withAlpha(color: Int, alpha: Int): Int =
        (color and 0x00FFFFFF) or ((alpha.coerceIn(0, 255)) shl 24)

    private fun dp(value: Float): Float =
        value * resources.displayMetrics.density
}
