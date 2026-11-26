package cl.caggrometal.deep33

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class Deep33FailoverRoutingTest {
    @Test
    fun primaryEndpointIsConfigured() {
        assertEquals(
            "https://deep33-backend.onrender.com",
            BuildConfig.DEEP33_PRIMARY_URL
        )
        assertTrue(BuildConfig.DEEP33_PRIMARY_URL.isNotBlank())
    }

    @Test
    fun secondaryEndpointUsesCanonicalDeep33TertiaryRoute() {
        assertEquals(
            "https://deep33-backup.onrender.com",
            BuildConfig.DEEP33_SECONDARY_URL
        )
        assertTrue(BuildConfig.DEEP33_SECONDARY_URL.isNotBlank())
    }

    @Test
    fun tertiaryEndpointUsesCanonicalRenderApiFallback() {
        assertEquals(
            "https://deep33-api.onrender.com",
            BuildConfig.DEEP33_TERTIARY_URL.trim()
        )
    }
}
