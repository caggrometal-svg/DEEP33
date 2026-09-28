package cl.caggrometal.deep33

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class Deep33FailoverPolicyTest {
    @Test
    fun serverFailureCanFailOverAfterPostBodyStarted() {
        assertTrue(
            Deep33FailoverPolicy.canFailover(
                method = "POST",
                requestBodyStarted = true,
                error = Deep33ApiException.Kind.SERVER
            )
        )
    }

    @Test
    fun timeoutCannotFailOverAfterPostBodyStarted() {
        assertFalse(
            Deep33FailoverPolicy.canFailover(
                method = "POST",
                requestBodyStarted = true,
                error = Deep33ApiException.Kind.TIMEOUT
            )
        )
    }

    @Test
    fun networkFailureCannotFailOverAfterPostBodyStarted() {
        assertFalse(
            Deep33FailoverPolicy.canFailover(
                method = "POST",
                requestBodyStarted = true,
                error = Deep33ApiException.Kind.NETWORK
            )
        )
    }

    @Test
    fun timeoutCanFailOverBeforePostBodyStarts() {
        assertTrue(
            Deep33FailoverPolicy.canFailover(
                method = "POST",
                requestBodyStarted = false,
                error = Deep33ApiException.Kind.TIMEOUT
            )
        )
    }

    @Test
    fun timeoutCanFailOverForGet() {
        assertTrue(
            Deep33FailoverPolicy.canFailover(
                method = "GET",
                requestBodyStarted = false,
                error = Deep33ApiException.Kind.TIMEOUT
            )
        )
    }

    @Test
    fun http5xxIsAFailoverEligibleServerFailure() {
        val error = Deep33Api.mapError(503)
        assertEquals(Deep33ApiException.Kind.SERVER, error.kind)
        assertTrue(
            Deep33FailoverPolicy.canFailover(
                method = "POST",
                requestBodyStarted = true,
                error = error.kind
            )
        )
    }

    @Test
    fun authRateLimitBadResponseAndCancelNeverFailOver() {
        val blocked = listOf(
            Deep33ApiException.Kind.AUTH,
            Deep33ApiException.Kind.RATE_LIMIT,
            Deep33ApiException.Kind.BAD_RESPONSE,
            Deep33ApiException.Kind.CANCELLED
        )
        blocked.forEach { kind ->
            assertFalse(
                kind.toString(),
                Deep33FailoverPolicy.canFailover(
                    method = "GET",
                    requestBodyStarted = false,
                    error = kind
                )
            )
        }
    }
}
