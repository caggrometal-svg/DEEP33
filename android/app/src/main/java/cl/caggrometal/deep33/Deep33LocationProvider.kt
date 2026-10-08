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
    val label: String?
) {
    fun asPromptContext(): String {
        val readable = label?.takeIf { it.isNotBlank() }?.let { "$it; " } ?: ""
        val coarseLat = "%.2f".format(Locale.US, latitude)
        val coarseLon = "%.2f".format(Locale.US, longitude)
        return "[UBICACIÓN LOCAL ACTUAL — uso interno para consultas locales: ${readable}lat≈$coarseLat, lon≈$coarseLon]"
    }
}

object Deep33LocationProvider {
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
        includeLabel: Boolean = true
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
        if (providers.isEmpty()) {
            callback(null)
            return
        }

        val executor = Executors.newSingleThreadExecutor()
        val completed = AtomicBoolean(false)
        val candidates = java.util.concurrent.CopyOnWriteArrayList<Location>()
        val deadline = android.os.SystemClock.elapsedRealtime() + LOCATION_TIMEOUT_MS

        fun chooseBest(): Location? {
            val now = System.currentTimeMillis()
            return candidates
                .filter { location ->
                    val age = now - location.time
                    age in 0..MAX_LOCATION_AGE_MS &&
                        location.latitude in -90.0..90.0 &&
                        location.longitude in -180.0..180.0 &&
                        (!location.hasAccuracy() || location.accuracy.isFinite())
                }
                .minWithOrNull(compareBy<Location>(
                    { if (it.hasAccuracy()) it.accuracy else Float.MAX_VALUE },
                    { now - it.time }
                ))
        }

        fun finish() {
            if (!completed.compareAndSet(false, true)) return
            val best = chooseBest()
            executor.shutdownNow()
            if (best == null) {
                callback(null)
                return
            }
            if (!includeLabel) {
                callback(Deep33LocationContext(best.latitude, best.longitude, null))
                return
            }
            Executors.newSingleThreadExecutor().execute {
                val label = reverseGeocode(context, best)
                android.os.Handler(android.os.Looper.getMainLooper()).post {
                    callback(Deep33LocationContext(best.latitude, best.longitude, label))
                }
            }
        }

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            val callbackCount = java.util.concurrent.atomic.AtomicInteger(providers.size)
            providers.forEach { provider ->
                runCatching {
                    manager.getCurrentLocation(
                        provider,
                        null,
                        executor
                    ) { location ->
                        if (location != null) candidates.add(location)
                        callbackCount.decrementAndGet()
                        val best = chooseBest()
                        val now = android.os.SystemClock.elapsedRealtime()
                        if (
                            best != null &&
                            best.hasAccuracy() &&
                            best.accuracy <= TARGET_ACCURACY_METERS
                        ) {
                            finish()
                        } else if (callbackCount.get() == 0 || now >= deadline) {
                            finish()
                        }
                    }
                }.onFailure {
                    callbackCount.decrementAndGet()
                    if (callbackCount.get() == 0) finish()
                }
            }
            executor.execute {
                val remaining = (deadline - android.os.SystemClock.elapsedRealtime()).coerceAtLeast(1L)
                runCatching { Thread.sleep(remaining) }
                finish()
            }
        } else {
            providers.forEach { provider ->
                runCatching { manager.getLastKnownLocation(provider) }
                    .getOrNull()
                    ?.let { candidates.add(it) }
            }
            finish()
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
                address.countryName
            )
                .filter { !it.isNullOrBlank() }
                .distinct()
                .joinToString(", ")
                .ifBlank { null }
        }.getOrNull()
    }
    private const val LOCATION_TIMEOUT_MS = 2_500L
    private const val MAX_LOCATION_AGE_MS = 5 * 60 * 1000L
    private const val TARGET_ACCURACY_METERS = 250f

}
