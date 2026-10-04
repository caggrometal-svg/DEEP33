package cl.caggrometal.deep33

import java.util.UUID

/**
 * Identity used to isolate one DEEP33 installation from every other installation.
 *
 * This is deliberately an opaque random identifier rather than device hardware data.
 * It remains stable only because SessionStore persists it in app-private storage.
 */
internal object MultiUserIdentity {
    const val PROFILE_PREFIX = "profile-"

    fun newProfileId(): String =
        PROFILE_PREFIX + UUID.randomUUID().toString()

    fun newSessionId(): String =
        UUID.randomUUID().toString()
}
