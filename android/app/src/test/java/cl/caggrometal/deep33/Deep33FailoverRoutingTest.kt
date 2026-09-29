package cl.caggrometal.deep33

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class Deep33FailoverRoutingTest {
    @Test
    fun productionEndpointsAreAlwaysConfigured() {
        assertEquals("https://guqevsjbjyapqjjtutza.supabase.co/functions/v1/deep33-proxy", BuildConfig.DEEP33_PRIMARY_URL)
        assertEquals("https://guqevsjbjyapqjjtutza.supabase.co/functions/v1/deep33-proxy", BuildConfig.DEEP33_SECONDARY_URL)
        assertEquals("https://guqevsjbjyapqjjtutza.supabase.co/functions/v1/deep33-proxy", BuildConfig.DEEP33_TERTIARY_URL)
        assertTrue(BuildConfig.DEEP33_SECONDARY_URL.isNotBlank())
        assertTrue(BuildConfig.DEEP33_TERTIARY_URL.isNotBlank())
    }
}
