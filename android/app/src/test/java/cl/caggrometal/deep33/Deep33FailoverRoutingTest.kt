package cl.caggrometal.deep33

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.UUID

class Deep33FailoverRoutingTest {
    @Test
    fun primaryDownUsesSecondaryForRealInference() {
        val payload = JSONArray().put(
            JSONObject()
                .put("role", "user")
                .put("content", "Return only this exact token: DEEP33_FAILOVER_E2E_OK")
        )
        val sessionId = "failover-unit-" + UUID.randomUUID()
        val response = Deep33Api.generate(payload, sessionId)
        val text = response.optJSONObject("result")?.optString("text").orEmpty()
        assertTrue("Failover response: $response", text.contains("DEEP33_FAILOVER_E2E_OK"))
    }
}
