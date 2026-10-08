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
import androidx.annotation.RequiresApi
import java.util.Locale
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

data class Deep33LocationContext(
    val latitude: Double,
    val longitude: Double,
    val label: String?,
    val shareCoordinatesWithInference: Boolean = false
) {
    fun asPromptContext(): String {
        val readable = label?.takeIf { it.isNotBlank() }?.let { "$it; " } ?: ""
        val coordinates = if (shareCoordinatesWithInference) {
            "lat=${"%.6f".format(Locale.US, latitude)}, lon=${"%.6f".format(Locale.US, longitude)}"
        } else {
            "localidad"
        }
        return "[CONTEXTO GEOGRÁFICO TEMPORAL — no es historial personal: ${readable}$coordinates]"
    }
}

object Deep33LocationProvider {
    private const val LOCATION_DEADLINE_MS = 3_500L
    private const val MAX_LOCATION_AGE_MS = 120_000L
    private const val MAX_REASONABLE_ACCURACY_METERS = 5_000f
    private const val FRESHNESS_PENALTY_PER_SECOND = 0.35

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
        shareCoordinatesWithInference: Boolean = false
    ) {
        if (!hasPermission(context)) {
            callback(null)
            return
        }
        val manager = context.getSystemService(LocationManager::class.java) ?: run {
            callback(null)
            return
        }

        val providers = listOf(
            LocationManager.GPS_PROVIDER,
            LocationManager.NETWORK_PROVIDER
        ).filter { provider ->
            runCatching { manager.isProviderEnabled(provider) }.getOrDefault(false)
        }

        fun valid(location: Location?): Location? {
            if (location == null) return null
            if (!location.latitude.isFinite() || !location.longitude.isFinite()) return null
            if (location.latitude !in -90.0..90.0 || location.longitude !in -180.0..180.0) return null
            val accuracy = location.accuracy
            if (!accuracy.isFinite() || accuracy <= 0f || accuracy > MAX_REASONABLE_ACCURACY_METERS) return null
            return location
        }

        fun ageMs(location: Location): Long =
            (System.currentTimeMillis() - location.time).coerceAtLeast(0L)

        fun score(location: Location): Double {
            val ageSeconds = ageMs(location).toDouble() / 1000.0
            return location.accuracy.toDouble() + ageSeconds * FRESHNESS_PENALTY_PER_SECOND
        }

        fun finish(candidates: List<Location>) {
            val chosen = candidates
                .mapNotNull(::valid)
                .filter { ageMs(it) <= MAX_LOCATION_AGE_MS }
                .minByOrNull(::score)
            if (chosen == null) {
                callback(null)
                return
            }
            callback(
                Deep33LocationContext(
                    latitude = chosen.latitude,
                    longitude = chosen.longitude,
                    label = if (includeLabel) reverseGeocode(context, chosen) else null,
                    shareCoordinatesWithInference = shareCoordinatesWithInference
                )
            )
        }

        if (providers.isEmpty()) {
            callback(null)
            return
        }

        val callbackExecutor = java.util.concurrent.Executors.newSingleThreadExecutor()
        val deadlineExecutor = java.util.concurrent.Executors.newSingleThreadScheduledExecutor()
        val completed = AtomicBoolean(false)
        val candidates = java.util.Collections.synchronizedList(mutableListOf<Location>())
        val remaining = java.util.concurrent.atomic.AtomicInteger(providers.size)

        fun complete() {
            if (completed.compareAndSet(false, true)) {
                finish(candidates.toList())
                callbackExecutor.shutdownNow()
                deadlineExecutor.shutdownNow()
            }
        }

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            providers.forEach { provider ->
                runCatching {
                    manager.getCurrentLocation(
                        provider,
                        null,
                        callbackExecutor
                    ) { location ->
                        valid(location)?.let(candidates::add)
                        if (remaining.decrementAndGet() == 0) complete()
                    }
                }.onFailure {
                    if (remaining.decrementAndGet() == 0) complete()
                }
            }
        } else {
            providers.forEach { provider ->
                runCatching { valid(manager.getLastKnownLocation(provider)) }
                    .getOrNull()
                    ?.let(candidates::add)
            }
            complete()
            return
        }

        deadlineExecutor.schedule(
            { complete() },
            LOCATION_DEADLINE_MS,
            java.util.concurrent.TimeUnit.MILLISECONDS
        )
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
                address.countryName
            )
                .filter { !it.isNullOrBlank() }
                .distinct()
                .joinToString(", ")
                .ifBlank { null }
        }.getOrNull()
    }
}
