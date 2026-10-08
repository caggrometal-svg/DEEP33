package cl.caggrometal.deep33

import android.content.Context
import java.util.UUID

data class LocalUserProfile(
    val id: String,
    val displayName: String,
)

internal object MultiUserIdentity {
    const val PROFILE_PREFIX = "profile-"
    private const val PREFS = "deep33_user_profiles"
    private const val ACTIVE_ID = "active_profile_id"
    private const val PROFILE_IDS = "profile_ids"
    private const val NAME_PREFIX = "profile_name_"
    private const val DEFAULT_NAME_PREFIX = "Usuario "

    private val lock = Any()

    fun newProfileId(): String = PROFILE_PREFIX + UUID.randomUUID().toString()
    fun newSessionId(): String = UUID.randomUUID().toString()

    fun ensureActiveProfileId(context: Context): String = synchronized(lock) {
        val prefs = context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val active = prefs.getString(ACTIVE_ID, null)?.trim().orEmpty()
        if (active.isNotBlank()) return@synchronized active
        val created = newProfileId()
        val ids = prefs.getStringSet(PROFILE_IDS, emptySet()).orEmpty().toMutableSet()
        ids.add(created)
        prefs.edit()
            .putString(ACTIVE_ID, created)
            .putStringSet(PROFILE_IDS, ids)
            .putString(NAME_PREFIX + created, DEFAULT_NAME_PREFIX + ids.size)
            .commit()
        created
    }

    fun currentProfileId(context: Context): String = ensureActiveProfileId(context)

    fun currentProfile(context: Context): LocalUserProfile {
        val appContext = context.applicationContext
        val prefs = appContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val id = ensureActiveProfileId(appContext)
        val name = prefs.getString(NAME_PREFIX + id, null)?.trim().orEmpty()
        return LocalUserProfile(id, name.ifBlank { DEFAULT_NAME_PREFIX + "1" })
    }

    fun listProfiles(context: Context): List<LocalUserProfile> = synchronized(lock) {
        val appContext = context.applicationContext
        val prefs = appContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val active = ensureActiveProfileId(appContext)
        prefs.getStringSet(PROFILE_IDS, emptySet())
            .orEmpty()
            .map { id ->
                LocalUserProfile(
                    id = id,
                    displayName = prefs.getString(NAME_PREFIX + id, null)?.trim()
                        .orEmpty()
                        .ifBlank { DEFAULT_NAME_PREFIX + "1" },
                )
            }
            .sortedWith(compareBy({ it.id != active }, { it.displayName.lowercase() }))
    }

    fun createProfile(context: Context, requestedName: String?): LocalUserProfile = synchronized(lock) {
        val appContext = context.applicationContext
        val prefs = appContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        ensureActiveProfileId(appContext)
        val ids = prefs.getStringSet(PROFILE_IDS, emptySet()).orEmpty().toMutableSet()
        val id = newProfileId()
        ids.add(id)
        val fallback = DEFAULT_NAME_PREFIX + ids.size
        val name = requestedName?.trim()?.replace(Regex("\s+"), " ")?.take(40).orEmpty()
            .ifBlank { fallback }
        prefs.edit()
            .putStringSet(PROFILE_IDS, ids)
            .putString(NAME_PREFIX + id, name)
            .commit()
        LocalUserProfile(id, name)
    }

    fun switchProfile(context: Context, profileId: String): Boolean = synchronized(lock) {
        val appContext = context.applicationContext
        val prefs = appContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val id = profileId.trim()
        if (id.isBlank()) return@synchronized false
        val ids = prefs.getStringSet(PROFILE_IDS, emptySet()).orEmpty()
        if (id !in ids) return@synchronized false
        if (id == prefs.getString(ACTIVE_ID, null)) return@synchronized true
        prefs.edit().putString(ACTIVE_ID, id).commit()
        true
    }
}
