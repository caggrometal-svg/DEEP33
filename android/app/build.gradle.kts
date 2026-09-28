plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
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
    buildFeatures { buildConfig = true }
    val primaryUrl = System.getenv("DEEP33_PRIMARY_URL") ?: "https://deep33-backend.onrender.com"
    val secondaryUrl = System.getenv("DEEP33_SECONDARY_URL") ?: "https://iac33-fastapi-edge-production.up.railway.app"
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
    androidTestImplementation("androidx.test.ext:junit:1.2.1")
    androidTestImplementation("androidx.test:runner:1.6.2")
}
