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
        return "[UBICACIÓN GPS ACTUAL — uso interno para consultas locales: ${readable}lat=${"%.6f".format(Locale.US, latitude)}, lon=${"%.6f".format(Locale.US, longitude)}]"
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

        fun finish(location: Location?) {
            if (location == null) {
                callback(null)
                return
            }
            callback(
                Deep33LocationContext(
                    latitude = location.latitude,
                    longitude = location.longitude,
                    label = if (includeLabel) reverseGeocode(context, location) else null
                )
            )
        }

        val providers = listOf(
            LocationManager.GPS_PROVIDER,
            LocationManager.NETWORK_PROVIDER
        ).filter { provider ->
            runCatching { manager.isProviderEnabled(provider) }.getOrDefault(false)
        }

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            if (providers.isEmpty()) {
                callback(null)
                return
            }

            val callbackExecutor = Executors.newSingleThreadExecutor()
            val completed = AtomicBoolean(false)
            val remaining = java.util.concurrent.atomic.AtomicInteger(providers.size)

            fun report(location: Location?) {
                if (location != null && completed.compareAndSet(false, true)) {
                    finish(location)
                    callbackExecutor.shutdownNow()
                    return
                }

                if (
                    location == null &&
                    remaining.decrementAndGet() == 0 &&
                    completed.compareAndSet(false, true)
                ) {
                    callback(null)
                    callbackExecutor.shutdownNow()
                }
            }

            providers.forEach { provider ->
                runCatching {
                    manager.getCurrentLocation(
                        provider,
                        null,
                        callbackExecutor,
                        { location -> report(location) }
                    )
                }.onFailure {
                    report(null)
                }
            }
            return
        }

        val lastKnown = providers.asSequence()
            .mapNotNull { provider -> runCatching { manager.getLastKnownLocation(provider) }.getOrNull() }
            .maxByOrNull { it.time }
        finish(lastKnown)
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
