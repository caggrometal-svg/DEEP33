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
    fun allFailoverEndpointsAreConfiguredAndDistinct() {
        val primary = BuildConfig.DEEP33_PRIMARY_URL.trim()
        val secondary = BuildConfig.DEEP33_SECONDARY_URL.trim()
        val tertiary = BuildConfig.DEEP33_TERTIARY_URL.trim()

        assertTrue("DEEP33 secondary endpoint missing", secondary.isNotBlank())
        assertTrue("DEEP33 tertiary endpoint missing", tertiary.isNotBlank())

        assertNotEquals("Primary/secondary endpoint collision", primary, secondary)
        assertNotEquals("Primary/tertiary endpoint collision", primary, tertiary)
        assertNotEquals("Secondary/tertiary endpoint collision", secondary, tertiary)

        assertTrue("Secondary endpoint must be HTTPS", secondary.startsWith("https://"))
        assertTrue("Tertiary endpoint must be HTTPS", tertiary.startsWith("https://"))
    }
}
