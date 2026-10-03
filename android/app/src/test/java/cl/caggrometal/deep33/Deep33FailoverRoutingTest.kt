package cl.caggrometal.deep33

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class Deep33FailoverRoutingTest {
    @Test
    fun primaryEndpointIsCanonicalDeep33EdgeRoute() {
        assertEquals(
            "https://opocgzydeknuchtrqzfa.supabase.co/functions/v1/deep33-proxy",
            BuildConfig.DEEP33_PRIMARY_URL
        )
        assertTrue(BuildConfig.DEEP33_PRIMARY_URL.isNotBlank())
    }

    @Test
    fun secondaryEndpointUsesIndependentDeep33TertiaryRoute() {
        assertEquals(
            "https://opocgzydeknuchtrqzfa.supabase.co/functions/v1/deep33-tertiary",
            BuildConfig.DEEP33_SECONDARY_URL
        )
        assertTrue(BuildConfig.DEEP33_SECONDARY_URL.isNotBlank())
    }

    @Test
    fun tertiaryEndpointUsesRenderAsFinalFallback() {
        assertEquals(
            "https://deep33-backend.onrender.com",
            BuildConfig.DEEP33_TERTIARY_URL
        )
        assertTrue(BuildConfig.DEEP33_TERTIARY_URL.isNotBlank())
    }

    @Test
    fun allTransportEndpointsAreDistinctHttpsRoutes() {
        val endpoints = listOf(
            BuildConfig.DEEP33_PRIMARY_URL,
            BuildConfig.DEEP33_SECONDARY_URL,
            BuildConfig.DEEP33_TERTIARY_URL
        )
        assertEquals(3, endpoints.distinct().size)
        assertTrue(endpoints.all { it.startsWith("https://") })
    }
}
