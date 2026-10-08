package cl.caggrometal.deep33

import android.Manifest
import android.annotation.SuppressLint
import android.content.Context
import android.content.pm.PackageManager
import android.location.Address
import android.location.Geocoder
import android.location.Location
import android.location.LocationManager
import android.os.Build
import android.os.SystemClock
import java.util.Locale
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import kotlin.math.abs

data class Deep33LocationContext(
    val label: String?,
    val accuracyMeters: Float,
    val ageMs: Long,
) {
    fun asPromptContext(): String {
        val readable = label?.takeIf { it.isNotBlank() } ?: "localidad actual no resuelta"
        val precision = when {
            accuracyMeters <= 100f -> "alta"
            accuracyMeters <= 500f -> "media"
            else -> "aproximada"
        }
        return "[CONTEXTO LOCAL ACTUAL — uso temporal para consultas locales: $readable; precisión $precision]"
    }
}

object Deep33LocationProvider {
    private const val CURRENT_TIMEOUT_MS = 4_000L
    private const val MAX_AGE_MS = 5 * 60_000L
    private const val MAX_ACCURACY_METERS = 2_000f
    private const val LABEL_TIMEOUT_MS = 1_500L

    private val executor = Executors.newCachedThreadPool { runnable ->
        Thread(runnable, "DEEP33-Location").apply { isDaemon = true }
    }

    fun hasPermission(context: Context): Boolean =
        context.checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED ||
            context.checkSelfPermission(Manifest.permission.ACCESS_COARSE_LOCATION) == PackageManager.PERMISSION_GRANTED

    fun isLocationEnabled(context: Context): Boolean {
        val manager = context.getSystemService(LocationManager::class.java) ?: return false
        return runCatching {
            manager.isProviderEnabled(LocationManager.GPS_PROVIDER) ||
                manager.isProviderEnabled(LocationManager.NETWORK_PROVIDER)
        }.getOrDefault(false)
    }

    @SuppressLint("MissingPermission")
    fun resolve(
        context: Context,
        callback: (Deep33LocationContext?) -> Unit,
        includeLabel: Boolean = true,
    ) {
        if (!hasPermission(context)) {
            callback(null)
            return
        }
        val manager = context.getSystemService(LocationManager::class.java) ?: run {
            callback(null)
            return
        }

        executor.execute {
            val best = when {
                Build.VERSION.SDK_INT >= Build.VERSION_CODES.R -> currentLocationCandidate(manager)
                else -> lastKnownCandidate(manager)
            }
            if (best == null) {
                callback(null)
                return@execute
            }

            val ageMs = locationAgeMs(best)
            val label = if (includeLabel) reverseGeocodeWithTimeout(context, best) else null
            callback(
                Deep33LocationContext(
                    label = label,
                    accuracyMeters = best.accuracy,
                    ageMs = ageMs,
                )
            )
        }
    }

    @SuppressLint("MissingPermission")
    private fun currentLocationCandidate(manager: LocationManager): Location? {
        val providers = enabledProviders(manager)
        if (providers.isEmpty()) return null

        val results = mutableListOf<Location>()
        val lock = Any()
        val latch = CountDownLatch(providers.size)
        val callbackExecutor = Executors.newFixedThreadPool(providers.size)

        providers.forEach { provider ->
            runCatching {
                manager.getCurrentLocation(
                    provider,
                    null,
                    callbackExecutor,
                ) { location ->
                    synchronized(lock) {
                        if (location != null) results.add(location)
                    }
                    latch.countDown()
                }
            }.onFailure {
                latch.countDown()
            }
        }

        latch.await(CURRENT_TIMEOUT_MS, TimeUnit.MILLISECONDS)
        callbackExecutor.shutdownNow()

        return results
            .asSequence()
            .filter(::isAcceptable)
            .minWithOrNull(
                compareBy<Location>({ accuracyScore(it) }, { locationAgeMs(it) })
            )
    }

    @SuppressLint("MissingPermission")
    private fun lastKnownCandidate(manager: LocationManager): Location? {
        return enabledProviders(manager)
            .mapNotNull { provider ->
                runCatching { manager.getLastKnownLocation(provider) }.getOrNull()
            }
            .filter(::isAcceptable)
            .minWithOrNull(
                compareBy<Location>({ accuracyScore(it) }, { locationAgeMs(it) })
            )
    }

    private fun enabledProviders(manager: LocationManager): List<String> =
        listOf(LocationManager.GPS_PROVIDER, LocationManager.NETWORK_PROVIDER)
            .filter { provider ->
                runCatching { manager.isProviderEnabled(provider) }.getOrDefault(false)
            }

    private fun isAcceptable(location: Location): Boolean {
        val age = locationAgeMs(location)
        val accuracy = if (location.hasAccuracy()) location.accuracy else Float.MAX_VALUE
        return age in 0..MAX_AGE_MS && accuracy <= MAX_ACCURACY_METERS
    }

    private fun accuracyScore(location: Location): Float =
        if (location.hasAccuracy()) location.accuracy else Float.MAX_VALUE

    private fun locationAgeMs(location: Location): Long {
        val nowElapsedNs = SystemClock.elapsedRealtimeNanos()
        val locationElapsedNs = runCatching { location.elapsedRealtimeNanos }.getOrDefault(0L)
        return if (locationElapsedNs > 0L) {
            maxOf(0L, (nowElapsedNs - locationElapsedNs) / 1_000_000L)
        } else {
            maxOf(0L, System.currentTimeMillis() - location.time)
        }
    }

    private fun reverseGeocodeWithTimeout(context: Context, location: Location): String? {
        val future = executor.submit<String?> { reverseGeocode(context, location) }
        return runCatching {
            future.get(LABEL_TIMEOUT_MS, TimeUnit.MILLISECONDS)
        }.getOrNull().also {
            if (it == null) future.cancel(true)
        }
    }

    private fun reverseGeocode(context: Context, location: Location): String? {
        if (!Geocoder.isPresent()) return null
        return runCatching {
            @Suppress("DEPRECATION")
            val addresses: List<Address> = Geocoder(context, Locale("es", "CL"))
                .getFromLocation(location.latitude, location.longitude, 1)
                ?: emptyList()
            val address = addresses.firstOrNull() ?: return@runCatching null
            listOf(
                address.subLocality,
                address.locality,
                address.adminArea,
                address.countryName,
            )
                .filter { !it.isNullOrBlank() }
                .distinct()
                .joinToString(", ")
                .ifBlank { null }
        }.getOrNull()
    }
}
