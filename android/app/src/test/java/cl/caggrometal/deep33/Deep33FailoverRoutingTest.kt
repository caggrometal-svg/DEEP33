package cl.caggrometal.deep33

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class Deep33FailoverRoutingTest {
    @Test
    fun primaryEndpointIsConfigured() {
        assertEquals(
            "https://guqevsjbjyapqjjtutza.supabase.co/functions/v1/deep33-proxy",
            BuildConfig.DEEP33_PRIMARY_URL
        )
        assertTrue(BuildConfig.DEEP33_PRIMARY_URL.isNotBlank())
    }

    @Test
    fun configuredFailoverEndpointsAreDistinctWhenUsed() {
        val primary = BuildConfig.DEEP33_PRIMARY_URL.trim()
        val secondary = BuildConfig.DEEP33_SECONDARY_URL.trim()
        val tertiary = BuildConfig.DEEP33_TERTIARY_URL.trim()

        if (BuildConfig.DEEP33_REQUIRE_REDUNDANCY) {
            assertTrue("Secondary failover endpoint is required", secondary.isNotBlank())
            assertTrue("Tertiary failover endpoint is required", tertiary.isNotBlank())
        } else if (secondary.isBlank() && tertiary.isBlank()) {
            return
        }

        if (secondary.isNotBlank()) assertNotEquals(primary, secondary)
        if (tertiary.isNotBlank()) assertNotEquals(primary, tertiary)
        if (secondary.isNotBlank() && tertiary.isNotBlank()) assertNotEquals(secondary, tertiary)
    }
}
