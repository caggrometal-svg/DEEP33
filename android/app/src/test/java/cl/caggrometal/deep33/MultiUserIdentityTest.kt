package cl.caggrometal.deep33

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class MultiUserIdentityTest {

    @Test
    fun profileIdsAreOpaqueAndUniquePerInstallation() {
        val first = MultiUserIdentity.newProfileId()
        val second = MultiUserIdentity.newProfileId()

        assertTrue(first.startsWith(MultiUserIdentity.PROFILE_PREFIX))
        assertTrue(first.length > MultiUserIdentity.PROFILE_PREFIX.length)
        assertNotEquals(first, second)
        assertEquals(5 + 36, first.length)
    }

    @Test
    fun sessionIdsAreUnique() {
        val first = MultiUserIdentity.newSessionId()
        val second = MultiUserIdentity.newSessionId()

        assertNotEquals(first, second)
        assertEquals(36, first.length)
    }
}
