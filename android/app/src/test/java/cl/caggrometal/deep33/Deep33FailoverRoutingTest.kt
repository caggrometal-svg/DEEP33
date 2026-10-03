package cl.caggrometal.deep33

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class Deep33FailoverRoutingTest {
    @Test
    fun primaryEndpointIsConfigured() {
        assertEquals(
            "https://opocgzydeknuchtrqzfa.supabase.co/functions/v1/deep33-tertiary",
            BuildConfig.DEEP33_PRIMARY_URL
        )
        assertTrue(BuildConfig.DEEP33_PRIMARY_URL.isNotBlank())
    }

    @Test
    fun secondaryEndpointUsesCanonicalDeep33ProxyRoute() {
        assertEquals(
            "https://opocgzydeknuchtrqzfa.supabase.co/functions/v1/deep33-proxy",
            BuildConfig.DEEP33_SECONDARY_URL
        )
        assertTrue(BuildConfig.DEEP33_SECONDARY_URL.isNotBlank())
    }

    @Test
    fun tertiaryEndpointIsBlankOrUsesConfiguredDeep33TertiaryRoute() {
        val tertiary = BuildConfig.DEEP33_TERTIARY_URL.trim()
        assertTrue(
            tertiary == "https://deep33-backend.onrender.com"
        )
    }
}
