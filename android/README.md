# DEEP33 Android

Cliente Android mínimo conectado al backend real de DEEP33.

## Backend

- Base URL: https://deep33-backend.onrender.com
- GET /health
- GET /v1/network/status
- GET /v1/ai/diagnostics
- POST /v1/chat
- POST /v1/chat/stream

No hay credenciales de proveedores en el APK.

## Build local

Requiere JDK 17 y Gradle 8.9.

gradle :app:assembleDebug

## E2E

Las pruebas instrumentadas ejercitan:

Android → Internet → DEEP33 Backend → AI Gateway → Model

La fase 3 se cierra únicamente con el job Android E2E en PASS.
