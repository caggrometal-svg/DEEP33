package cl.caggrometal.deep33

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class Deep33FailoverRoutingTest {
    @Test
    fun productionEndpointsAreAlwaysConfigured() {
        assertEquals("https://deep33-backend.onrender.com", BuildConfig.DEEP33_PRIMARY_URL)
        assertEquals("https://deep33-backup.onrender.com", BuildConfig.DEEP33_SECONDARY_URL)
        assertEquals("https://deep33-api.onrender.com", BuildConfig.DEEP33_TERTIARY_URL)
        assertTrue(BuildConfig.DEEP33_SECONDARY_URL.isNotBlank())
        assertTrue(BuildConfig.DEEP33_TERTIARY_URL.isNotBlank())
        assertFalse(BuildConfig.DEEP33_SECONDARY_URL == BuildConfig.DEEP33_PRIMARY_URL)
        assertFalse(BuildConfig.DEEP33_TERTIARY_URL == BuildConfig.DEEP33_PRIMARY_URL)
    }
}
