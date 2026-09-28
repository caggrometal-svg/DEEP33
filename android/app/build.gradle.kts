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
        versionCode = 3
        versionName = "0.3.0"
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }
    buildTypes {
        release { isMinifyEnabled = false }
    }
    buildFeatures {
        buildConfig = true
        compose = true
    }
    val primaryUrl = System.getenv("DEEP33_PRIMARY_URL") ?: "https://deep33-backend.onrender.com"
    val secondaryUrl = System.getenv("DEEP33_SECONDARY_URL") ?: "https://deep33-backup.onrender.com"
    buildTypes.all {
        buildConfigField("String", "DEEP33_PRIMARY_URL", "\"$primaryUrl\"")
        buildConfigField("String", "DEEP33_SECONDARY_URL", "\"$secondaryUrl\"")
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
    androidTestImplementation("androidx.test.ext:junit:1.2.1")
    androidTestImplementation("androidx.test:runner:1.6.2")
}
