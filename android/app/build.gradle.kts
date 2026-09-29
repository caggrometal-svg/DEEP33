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
        versionCode = 4
        versionName = "0.4.0"
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

    val primaryUrl = System.getenv("DEEP33_PRIMARY_URL")
        ?.trim()
        ?.takeIf { it.isNotBlank() }
        ?: "https://guqevsjbjyapqjjtutza.supabase.co/functions/v1/deep33-proxy"

    val secondaryUrl = System.getenv("DEEP33_SECONDARY_URL")
        ?.trim()
        ?.takeIf { it.isNotBlank() }
        ?: "https://opocgzydeknuchtrqzfa.supabase.co/functions/v1/deep33-proxy"

    val tertiaryUrl = System.getenv("DEEP33_TERTIARY_URL")
        ?.trim()
        ?.takeIf { it.isNotBlank() }
        ?: "https://opocgzydeknuchtrqzfa.supabase.co/functions/v1/deep33-tertiary"

    buildTypes.all {
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
    androidTestImplementation("androidx.test.ext:junit:1.3.0")
    androidTestImplementation("androidx.test:runner:1.7.0")
}
