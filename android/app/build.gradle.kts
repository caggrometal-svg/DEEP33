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
        buildConfigField("String", "DEEP33_SUPABASE_URL", "\"https://opocgzydeknuchtrqzfa.supabase.co\"")
        buildConfigField("String", "DEEP33_SUPABASE_PUBLISHABLE_KEY", "\"sb_publishable_7SoFLxFrXYC7IvEuWYLi2w_PglkkzKA\"")
        versionCode = 10
        versionName = "0.5.2"
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }

    val releaseKeystorePath = System.getenv("DEEP33_RELEASE_KEYSTORE_PATH")?.trim().orEmpty()
    val releaseStorePassword = System.getenv("DEEP33_RELEASE_STORE_PASSWORD")?.trim().orEmpty()
    val releaseKeyAlias = System.getenv("DEEP33_RELEASE_KEY_ALIAS")?.trim().orEmpty()
    val releaseKeyPassword = System.getenv("DEEP33_RELEASE_KEY_PASSWORD")?.trim().orEmpty()
    val releaseSigningValues = listOf(
        "DEEP33_RELEASE_KEYSTORE_PATH" to releaseKeystorePath,
        "DEEP33_RELEASE_STORE_PASSWORD" to releaseStorePassword,
        "DEEP33_RELEASE_KEY_ALIAS" to releaseKeyAlias,
        "DEEP33_RELEASE_KEY_PASSWORD" to releaseKeyPassword,
    )

    signingConfigs {
        create("release") {
            if (releaseSigningValues.all { it.second.isNotBlank() }) {
                storeFile = file(releaseKeystorePath)
                storePassword = releaseStorePassword
                keyAlias = releaseKeyAlias
                keyPassword = releaseKeyPassword
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            signingConfig = signingConfigs.getByName("release")
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
        }
    }

    // A release task must never produce an unsigned or ambiguously signed artifact.
    gradle.taskGraph.whenReady {
        val buildingRelease = allTasks.any { it.name.contains("Release", ignoreCase = true) }
        if (buildingRelease) {
            val missing = releaseSigningValues
                .filter { it.second.isBlank() }
                .map { it.first }
            require(missing.isEmpty()) {
                "Release signing configuration missing: ${missing.joinToString()}"
            }
            require(file(releaseKeystorePath).isFile) {
                "Release keystore does not exist at DEEP33_RELEASE_KEYSTORE_PATH"
            }
        }
    }

    buildFeatures {
        buildConfig = true
        compose = true
    }

    fun quoteBuildConfig(value: String): String =
        "\"" + value.replace("\\", "\\\\").replace("\"", "\\\"") + "\""

    // Render is the canonical backend cluster: primary, backup, and API.
    // The endpoint contract is immutable by build environment.
    val canonicalPrimaryUrl = "https://deep33-backend.onrender.com"
    val canonicalSecondaryUrl = "https://deep33-backup.onrender.com"
    val canonicalTertiaryUrl = "https://deep33-api.onrender.com"

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
    val endpointByName = listOf(
        canonicalPrimaryUrl,
        canonicalSecondaryUrl,
        canonicalTertiaryUrl,
    ).withIndex().associate { (index, value) ->
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
        buildConfigField("String", "DEEP33_RENDER_URL", quoteBuildConfig(canonicalPrimaryUrl))
        buildConfigField("String", "DEEP33_PRIMARY_URL", quoteBuildConfig(primaryUrl))
        buildConfigField("String", "DEEP33_SECONDARY_URL", quoteBuildConfig(secondaryUrl))
        buildConfigField("String", "DEEP33_TERTIARY_URL", quoteBuildConfig(tertiaryUrl))
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
    testImplementation("org.json:json:20240303")
    androidTestImplementation("androidx.test.ext:junit:1.3.0")
    androidTestImplementation("androidx.test:runner:1.7.0")
    androidTestImplementation("androidx.test:core:1.7.0")
}
