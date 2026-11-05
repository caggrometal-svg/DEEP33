# DEEP33 — Auditoría de fallos de Vercel (2026-10-27)

## Alcance y estado verificado

Proyecto: `deep33-backend`  
Proyecto Vercel: `prj_shKD6xSwpM9P31IwsrIySP4Dq3NS`  
Repositorio: `caggrometal-svg/DEEP33`  
Rama bajo validación: `fix/realtime-web-evidence-271026`  
SHA de PR #196 observado en esta auditoría: `dd5b134f7c5944b4d817226fbbc47f0e2d9218e0`  
Producción observada: `b393f2dae5bfdfe61d9698860ddac0981c5c2399`  
PR #196 permanece abierto y en borrador. No se ha promovido a producción la rama.

## Hallazgos confirmados

### 1. Límite diario de despliegues del plan gratuito

El intento de crear el despliegue para el SHA exacto de PR #196 fue rechazado por Vercel con HTTP 402 y el código `api-deployments-free-per-day`; el mensaje indicó que se había superado el límite de más de 100 despliegues por día y que había que reintentar en 24 horas. GitHub registró en ese SHA un estado Vercel fallido: `Deployment rate limited — retry in 24 hours.`

**Clasificación:** bloqueo de cuota de plataforma, no error de compilación ni evidencia de defecto en el código. No repetir llamadas de despliegue mientras siga activo el límite.

### 2. El gate principal consultaba el endpoint equivocado de GitHub

En `.github/workflows/deep33_master_pipeline.yml`, el paso antiguo `Resolve exact Vercel deployment for this commit` consultaba `/commits/<sha>/check-runs` y buscaba una salida de una app llamada `vercel` con un patrón `open-feedback/<alias>`. Los datos observados de Vercel estaban en el endpoint de **commit statuses**, `/commits/<sha>/status`, con `context: "Vercel"`; no en el check-run que buscaba el script.

En el run `40545096429`, el checkout del SHA pasó, la búsqueda repitió 36 veces y terminó con `VERCEL_COMMIT_ALIAS_NOT_FOUND`. La etapa autenticada de Vercel, búsqueda web en vivo y SSE quedó omitida.

**Clasificación:** defecto de automatización de CI, separado del límite de cuota. El script esperaba una forma de datos incorrecta y consumía aproximadamente tres minutos antes de fallar. Se cambia a leer el estado Vercel del commit, fallar de inmediato si Vercel reporta cuota/error y solo entonces construir el alias de Preview conocido para esta rama. El gate posterior exige que `/health` declare el mismo SHA exacto, así que la construcción del alias no se considera prueba de despliegue por sí sola.

### 3. Exceso de despliegues por iteraciones y SHAs repetidos

La consulta del historial reciente devolvió 25 despliegues cancelados y ningún registro con estado `ERROR` o `BLOCKED` en el conjunto consultado. El listado de producción devolvió 14 despliegues y mostró SHA repetidos, incluidos seis registros de `01d120b2dceebb3c3ec8b84dfd9575c700c45208`, dos de `c162d0010bf9865994c0a1bf562436fbfcf132be` y dos de `54d46f6f9e8db3fb0a2eb108b9b2af03d4ae601c`.

**Clasificación:** desperdicio verificable de cuota por re-despliegues duplicados; la razón individual de cada cancelación no se puede confirmar con los eventos disponibles. Las correcciones pequeñas subidas una por una permiten que la integración Git de Vercel arranque builds repetidos. A partir de esta auditoría, agrupar cambios relacionados en un solo commit y no usar despliegues manuales adicionales cuando la integración Git ya inició el build.

### 4. Protección SSO de Preview

La configuración consultada informa `ssoProtection.enabled: true` para `deploymentType: "preview"`; la protección por contraseña está desactivada. La verificación actual del gate ya envía el header `x-vercel-protection-bypass` y detecta respuestas 302/401/403.

**Clasificación:** riesgo de configuración que puede bloquear los smoke tests si el secreto/entorno no coincide. No fue la causa directa del run fallido analizado: ese run terminó en la etapa de resolución del alias y no llegó a hacer peticiones HTTP al preview. No quitar protección de seguridad a ciegas; validar el bypass en la primera petición HTTP.

### 5. Redirecciones externas no son el mismo fallo que SSO

En la ventana de runtime logs que sí estuvo accesible apareció un `302 Found` desde `https://www.python.org/downloads/latest`. Es una redirección de un origen externo durante la navegación, no una evidencia de redirección SSO de Vercel.

**Clasificación:** redirección de URL fuente; el fetcher debe seguirla de forma segura, validar la URL final y extraer el contenido final.

### 6. Advertencia de rewrites del framework FastAPI

Los logs del build muestran que la compilación terminó correctamente en aproximadamente 9–10 segundos. También muestran la advertencia de Vercel sobre cambios en el comportamiento de rewrites internos en proyectos de backend. El archivo `vercel.json` redirige `/health`, `/ready`, `/liveness`, `/metrics` y `/v1/*` a `/api/*`.

**Clasificación:** advertencia de compatibilidad, no fallo de compilación observado. Hay que probar los contratos HTTP de esas rutas después de un despliegue; no afirmar que la advertencia causó el límite de cuota o el fallo del resolver.

### 7. Límites de los datos históricos

No se encontraron clústeres de runtime error para la ventana consultada de siete días, pero la lectura de logs crudos de siete días fue rechazada por el plan Hobby/retención de logs. Las consultas recientes sí respondieron. Por tanto, no existe base para afirmar que todos los fallos históricos de runtime hayan sido reconstruidos exhaustivamente solo desde logs.

## Protocolo obligatorio para próximos despliegues

1. **Un único candidato:** cerrar el diff, correr pruebas y reunir cambios relacionados en un solo commit antes de disparar el build.
2. **Preflight de GitHub/Vercel:** revisar el commit status `context == "Vercel"`. Si indica `rate limited`, `retry in 24 hours` o `build-rate-limit`, parar ahí; no volver a llamar a la API de deployments.
3. **Un solo build por SHA:** dejar que la integración Git cree el Preview. No disparar a la vez build automático, redeploy manual y create-deployment API.
4. **Identidad antes de funcionalidad:** resolver la URL de Preview y exigir que `GET /health` devuelva HTTP 200, `status == PASS`, `git_sha == SHA probado` y `build_id` presente. URL o build Ready sin coincidencia de SHA no sirve.
5. **Pruebas con protección:** comprobar que el header de bypass funciona; bloquear explícitamente 302/401/403 en endpoints DEEP33 y nunca seguir una redirección de protección enviando credenciales a otro host.
6. **Orden de smoke tests:** `/health` → `/ready` → `/v1/web/status` → `/v1/web/search` con consulta en vivo → extracción de una página fuente → `/v1/chat/stream` con SSE real → E2E Android. No saltar la extracción de páginas ni considerar éxito solo la búsqueda.
7. **Fallos clasificados, no reintentos ciegos:** cuota/plataforma, protección HTTP, build, readiness, API de navegación y E2E deben producir códigos de fallo diferentes. Si la causa es externa, parar rápido y conservar el SHA, URL, timestamp y mensaje.
8. **Conservar evidencia reciente:** en plan Hobby los logs históricos pueden expirar pronto; guardar el resumen de errores y los logs relevantes justo después de cada gate fallido.
9. **Promoción a producción:** solo tras todos los gates obligatorios en el mismo SHA y verificación de la app Android; si un gate quedó `skipped`, la versión no está certificada.

## Resultado de esta intervención

Se actualizan las tres puertas CI para consultar los commit statuses de GitHub y fallar de inmediato cuando Vercel informa una cuota o un despliegue fallido. Se agrega este registro para futuras entregas. La corrección del CI **no elimina** el límite de cuota actual ni confirma que el SHA `dd5b134f7c5944b4d817226fbbc47f0e2d9218e0` esté desplegado; ese SHA debe volver a desplegarse y pasar las pruebas cuando la plataforma lo permita.
