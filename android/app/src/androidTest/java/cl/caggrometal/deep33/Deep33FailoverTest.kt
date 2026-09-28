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
    fun primaryDownFallsBackToRailwayBackup() {
        val sessionId = "android-failover-" + UUID.randomUUID()
        val messages = JSONArray().put(
            JSONObject().put("role", "user").put("content", "Return only this exact token: DEEP33_FAILOVER_E2E_OK")
        )
        val response = Deep33Api.generate(messages, sessionId, "NEUTRO")
        val value = response.getJSONObject("result").optString("text")
        assertTrue("Unexpected failover response: $response", value.contains("DEEP33_FAILOVER_E2E_OK"))
    }
}
