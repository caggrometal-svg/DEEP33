package cl.caggrometal.deep33

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class Deep33FailoverRoutingTest {
    @Test
    fun primaryRenderBackendIsTheOnlyConfiguredEndpoint() {
        assertEquals("https://deep33-backend.onrender.com", BuildConfig.DEEP33_PRIMARY_URL)
        assertTrue(BuildConfig.DEEP33_SECONDARY_URL.isBlank())
    }
}
