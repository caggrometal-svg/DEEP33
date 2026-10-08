plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
}

android {
    namespace = "cl.caggrometal.deep33"
    compileSdk = 35

    defaultConfig {
        applicationId = "cl.caggrometal.deep33"
        minSdk = 26
        targetSdk = 35
        buildConfigField("boolean", "DEEP33_MULTIUSER_MODE", "true")
        versionCode = 8
        versionName = "0.5.0"
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }

    val releaseKeystorePath = System.getenv("DEEP33_RELEASE_KEYSTORE_PATH")?.trim().orEmpty()
    val releaseStorePassword = System.getenv("DEEP33_RELEASE_STORE_PASSWORD")?.trim().orEmpty()
    val releaseKeyAlias = System.getenv("DEEP33_RELEASE_KEY_ALIAS")?.trim().orEmpty()
    val releaseKeyPassword = System.getenv("DEEP33_RELEASE_KEY_PASSWORD")?.trim().orEmpty()
    val releaseSigningReady = listOf(
        releaseKeystorePath,
        releaseStorePassword,
        releaseKeyAlias,
        releaseKeyPassword,
    ).all { it.isNotBlank() }

    signingConfigs {
        create("release") {
            if (releaseSigningReady) {
                storeFile = file(releaseKeystorePath)
                storePassword = releaseStorePassword
                keyAlias = releaseKeyAlias
                keyPassword = releaseKeyPassword
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            if (releaseSigningReady) {
                signingConfig = signingConfigs.getByName("release")
            }
        }
    }

    buildFeatures {
        buildConfig = true
        compose = true
    }

    fun quoteBuildConfig(value: String): String =
        "\"" + value.replace("\\", "\\\\").replace("\"", "\\\"") + "\""

    // Canonical DEEP33 transport order: Edge primary, independent Edge failover,
    // Render backend as final transport fallback.
    // Transport contract is immutable by build environment. The three URLs below are
    // the only DEEP33 application routes; CI may repeat them for verification, but may
    // never replace them silently.
    val canonicalPrimaryUrl =
        "https://opocgzydeknuchtrqzfa.supabase.co/functions/v1/deep33-proxy"
    val canonicalSecondaryUrl =
        "https://opocgzydeknuchtrqzfa.supabase.co/functions/v1/deep33-tertiary"
    val canonicalTertiaryUrl = "https://deep33-backend.onrender.com"

    val suppliedEndpoints = listOf(
        "DEEP33_PRIMARY_URL" to System.getenv("DEEP33_PRIMARY_URL")?.trim().orEmpty(),
        "DEEP33_SECONDARY_URL" to System.getenv("DEEP33_SECONDARY_URL")?.trim().orEmpty(),
        "DEEP33_TERTIARY_URL" to System.getenv("DEEP33_TERTIARY_URL")?.trim().orEmpty(),
    )
    val canonicalEndpoints = listOf(
        canonicalPrimaryUrl,
        canonicalSecondaryUrl,
        canonicalTertiaryUrl,
    )
    val endpointByName = canonicalEndpoints.withIndex().associate { (index, value) ->
        suppliedEndpoints[index].first to value
    }
    val unexpectedOverrides = suppliedEndpoints.filter { (name, value) ->
        value.isNotBlank() && value != endpointByName[name]
    }.map { it.first }
    require(unexpectedOverrides.isEmpty()) {
        "DEEP33 transport endpoints are immutable; rejected overrides: " +
            unexpectedOverrides.joinToString(",")
    }
    require(canonicalEndpoints.all { it.startsWith("https://") && !it.endsWith("/") }) {
        "DEEP33 endpoints must be HTTPS URLs without trailing slash"
    }
    require(canonicalEndpoints.distinct().size == canonicalEndpoints.size) {
        "DEEP33 endpoints must be distinct for transport redundancy"
    }

    val primaryUrl = canonicalPrimaryUrl
    val secondaryUrl = canonicalSecondaryUrl
    val tertiaryUrl = canonicalTertiaryUrl

    buildTypes.all {
        buildConfigField("String", "DEEP33_PRIMARY_URL", quoteBuildConfig(primaryUrl))
        buildConfigField("String", "DEEP33_SECONDARY_URL", quoteBuildConfig(secondaryUrl))
        buildConfigField("String", "DEEP33_TERTIARY_URL", quoteBuildConfig(tertiaryUrl))
        buildConfigField(
            "String",
            "DEEP33_AUTH_URL",
            quoteBuildConfig("https://opocgzydeknuchtrqzfa.supabase.co/functions/v1/deep33-auth")
        )
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions { jvmTarget = "17" }
}

dependencies {
    implementation("androidx.activity:activity-compose:1.10.1")
    implementation("androidx.compose.ui:ui:1.7.8")
    implementation("androidx.compose.material3:material3:1.3.1")
    implementation("androidx.compose.ui:ui-tooling-preview:1.7.8")
    testImplementation("junit:junit:4.13.2")
    androidTestImplementation("androidx.test.ext:junit:1.3.0")
    androidTestImplementation("androidx.test:runner:1.7.0")
    androidTestImplementation("androidx.test:core:1.7.0")
}
