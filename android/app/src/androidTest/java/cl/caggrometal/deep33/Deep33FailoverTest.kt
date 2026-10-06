package cl.caggrometal.deep33

import androidx.test.ext.junit.runners.AndroidJUnit4
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import java.util.UUID

@RunWith(AndroidJUnit4::class)
class Deep33FailoverTest {
    @Test
    fun primaryDownFallsBackToSecondary() {
        val sessionId = "android-failover-" + UUID.randomUUID()
        val messages = JSONArray().put(
            JSONObject().put("role", "user")
                .put("content", "Return only this exact token: DEEP33_FAILOVER_E2E_OK")
        )

        val simulatedPrimaryDown = "https://deep33-primary-down.invalid"
        val secondary = BuildConfig.DEEP33_SECONDARY_URL
        assertTrue("Secondary endpoint missing", secondary.isNotBlank())

        val response = Deep33Api.generate(
            messages = messages,
            sessionId = sessionId,
            personality = "NEUTRO",
            endpointOverride = listOf(simulatedPrimaryDown, secondary)
        )
        val result = response.optJSONObject("result")
        assertTrue("Unexpected failover result: $response", result != null)
        assertTrue("Unexpected failover role: $response", result?.optString("role") == "assistant")
        assertTrue("Unexpected failover text: $response", result?.optString("text").orEmpty().isNotBlank())
    }
    @Test
    fun primaryAndSecondaryDownFallBackToTertiary() {
        val sessionId = "android-tertiary-" + UUID.randomUUID()
        val messages = JSONArray().put(
            JSONObject().put("role", "user")
                .put("content", "Return only this exact token: DEEP33_TERTIARY_E2E_OK")
        )

        val deadPrimary = "https://127.0.0.1:65531"
        val deadSecondary = "https://127.0.0.1:65532"
        val tertiary = BuildConfig.DEEP33_TERTIARY_URL
        assertTrue("Tertiary endpoint missing", tertiary.isNotBlank())

        val response = Deep33Api.generate(
            messages = messages,
            sessionId = sessionId,
            personality = "NEUTRO",
            endpointOverride = listOf(deadPrimary, deadSecondary, tertiary)
        )
        val result = response.optJSONObject("result")
        assertTrue("Unexpected tertiary failover result: $response", result != null)
        assertTrue("Unexpected tertiary failover role: $response", result?.optString("role") == "assistant")
        assertTrue("Unexpected tertiary failover text: $response", result?.optString("text").orEmpty().isNotBlank())
    }

}
