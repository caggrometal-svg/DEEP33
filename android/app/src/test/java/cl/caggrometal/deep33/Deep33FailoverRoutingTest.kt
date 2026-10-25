package cl.caggrometal.deep33

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class Deep33FailoverRoutingTest {
    @Test
    fun primaryEndpointIsConfigured() {
        assertEquals(
            "https://deep33-backend-iac33.vercel.app",
            BuildConfig.DEEP33_PRIMARY_URL
        )
        assertTrue(BuildConfig.DEEP33_PRIMARY_URL.isNotBlank())
    }

    @Test
    fun secondaryEndpointUsesCanonicalDeep33TertiaryRoute() {
        assertEquals(
            "https://deep33-backend-git-main-iac33.vercel.app",
            BuildConfig.DEEP33_SECONDARY_URL
        )
        assertTrue(BuildConfig.DEEP33_SECONDARY_URL.isNotBlank())
    }

    @Test
    fun tertiaryEndpointUsesCanonicalVercelFallback() {
        assertEquals(
            "https://deep33-backend.vercel.app",
            BuildConfig.DEEP33_TERTIARY_URL.trim()
        )
    }
}
