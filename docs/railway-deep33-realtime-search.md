# DEEP33 en Railway: navegación web en tiempo real

## Aislamiento

Esta rama está preparada para un servicio DEEP33 dedicado en Railway. No apunta ni cambia el código, las variables, los despliegues o los contratos de Render. Tampoco reconfigura los servicios existentes de IAC33. El backend de DEEP33 se debe conectar a un servicio Railway nuevo o aislado, nunca reutilizando a ciegas un servicio que atienda IAC33.

## Servicio Railway

- Repositorio: `caggrometal-svg/DEEP33`
- Rama: `railway-deep33-realtime-search-20261012`
- Directorio raíz: raíz del repositorio.
- Build: el `Dockerfile` existente.
- Puerto: respetar la variable `PORT` que inyecta Railway.
- Healthcheck: `/health`.
- Política de reinicio recomendada: `ON_FAILURE`.

No se versionan credenciales. Configurar en el panel de Railway las variables de ejecución que el backend necesite (autenticación Supabase, memoria y proveedor de IA). No reutilizar claves de servicio como token de prueba ni incluir secretos en commits.

## Motor de búsqueda y navegación

La rama unifica el contrato de versión del motor en `1.4.0` y expone el estado del ciclo de herramientas y de búsqueda web general/en tiempo real. El orquestador trabaja con Bing, Google, Mojeek y DuckDuckGo, con consultas paralelas, presupuestos temporales, cortacircuitos y una consulta de recuperación cuando la primera oleada no devuelve evidencia suficiente.

El lector de páginas de los dos gateways Edge admite una URL de backend Railway opcional y privada al servidor:

- `DEEP33_RAILWAY_BACKEND_URL`: URL HTTPS del dominio generado por Railway (terminado en `.up.railway.app`).

Esta variable pertenece al entorno de las funciones Edge de Supabase, no al contenedor Railway. **No está activada por este commit.** Cuando se configure en una fase posterior, la navegación probará Railway primero y usará los destinos de respaldo existentes si falla. Sin la variable, se mantiene el backend principal actual. La lectura continúa con fuentes únicas por dominio, evita duplicar URL y dominios en una misma navegación y conserva los candidatos originales si no logra abrir páginas; no inventa texto de páginas.

## Gate en vivo

El workflow `.github/workflows/railway-deep33-validation.yml` ejecuta toda la batería de pruebas local y la comprobación de tipos de ambas funciones Edge. La batería en vivo se ejecuta solo cuando se configuran estas propiedades en GitHub Actions:

- Variable `DEEP33_RAILWAY_BASE_URL`: URL pública del nuevo backend Railway.
- Secreto `DEEP33_RAILWAY_TEST_TOKEN`: token de acceso de prueba válido para DEEP33.

El gate en vivo exige healthcheck, estado del motor `1.4.0`, búsqueda actual con varios dominios, marca de recuperación reciente y lectura real de una página mediante `/v1/web/fetch`. Si esas propiedades no están configuradas, el gate en vivo queda omitido; no se debe interpretar como prueba de producción.

## Límites de activación

Este commit no despliega servicios, no cambia secretos y no activa la URL Railway en Supabase Edge. Primero debe existir un backend Railway dedicado, con variables completas y pruebas reales satisfactorias. La integración de navegación se activa después mediante la variable Edge indicada arriba, manteniendo intacta la configuración de Render.
