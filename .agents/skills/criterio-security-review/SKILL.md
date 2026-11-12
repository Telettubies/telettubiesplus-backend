---
name: criterio-security-review
description: Revisión defensiva de seguridad del backend de Criterio (analizador de pliegos). Úsala al auditar o cambiar autenticación con Supabase, autorización y aislamiento multiorganización, consultas y vectores en PostgreSQL, almacenamiento de PDF (Supabase Storage o S3), carga de PDF/ZIP, prompts y salidas de Anthropic/NVIDIA, validación de citas, secretos, Docker/Caddy/CI/CD, o antes de abrir un PR que toque una frontera de confianza.
---

# Revisión de seguridad de Criterio

Revisión defensiva y basada en evidencia. No implementes correcciones salvo que la tarea lo autorice;
las reglas de Git, secretos y autorización están en `CLAUDE.md` y `AGENTS.md`.

## 1. Fuentes antes de opinar

1. Alcance pedido por el usuario; si no indica ninguno, el diff de la rama frente a `develop`
   (`git diff --merge-base origin/develop`) más los cambios sin commit.
2. `docs/PLAN.md` (decisiones vigentes, conflictos con el DAS, supuestos) y `docs/openapi.json`.
3. DAS v0.3 §6.6 (amenazas S-01 a S-14) e HU. No están en este repo: búscalos en
   `../telettubiesplus-frontend/` (`DAS_v0.3_*.pdf`, `Historias de usuario*.pdf`) o pídelos.
   Si no están disponibles, decláralo como brecha de evidencia.
4. Código y pruebas del flujo afectado. Nunca leas ni muestres `.env` reales ni valores de secretos.

Distingue siempre: **decisión aceptada** (ADR), **decisión del equipo fuera del DAS** (p. ej.
Supabase Storage en lugar de S3, ADR-005), **propuesta no aprobada** (P-xx, p. ej. P-22 RLS) y
**supuesto**. No exijas una propuesta no aprobada como si fuera requisito: recomiéndala como opción.

## 2. Superficie de ataque de Criterio

| Entrada | Componentes | Dónde mirar |
| --- | --- | --- |
| API HTTP autenticada | Caddy → FastAPI | `api/deps.py`, `api/routers/*`, `integrations/supabase.py`, `deploy/Caddyfile` |
| Archivos subidos (PDF, JSON de conocimiento; ZIP si se incorpora) | API → almacenamiento → worker | `routers/licitaciones.py`, `routers/conocimiento.py`, `pipeline/lectura.py`, `integrations/almacenamiento.py` |
| Texto de documentos hacia modelos | worker → Anthropic / NVIDIA | `pipeline/extraccion.py`, `verificacion.py`, `filtrado.py`, `decision.py`, `integrations/*` |
| Mensajes de cola | RabbitMQ → worker | `messaging/*`, `worker/*` (solo identificadores, ADR-015) |
| Red y despliegue | Compose, EC2, GHCR, SSH | `docker-compose.prod.yml`, `Dockerfile`, `.github/workflows/*` |
| Secretos | todos | `core/config.py`, `.env*.example`, logs |

## 3. Lista de control (selecciona lo aplicable al alcance)

Referencias: OWASP ASVS 5.0, OWASP API Security Top 10 (2023), OWASP Top 10 for LLM Applications
(2025). Cita el identificador (p. ej. `API1:2023 BOLA`, `LLM01 Prompt Injection`) junto al S-/HU.

- **Autenticación (S-04, HU-B01).** Firma con JWKS, algoritmos permitidos fijos, `iss`, `aud`,
  `exp`/`nbf`, caché y fallo de JWKS (¿falla cerrado?). Token ausente, mal firmado, expirado o con
  audiencia errónea → 401. Mensajes que no revelen existencia de usuarios u organizaciones.
- **Autorización y multitenancy (S-03, S-05, HU-B04, ADR-014).** Organización y rol solo desde
  `miembros` en PostgreSQL; `X-Organizacion-Id` solo elige entre membresías verificadas. Cada
  consulta, join, búsqueda vectorial, URL firmada, trabajo de cola y etapa del worker filtra por
  `organizacion_id`. Recurso ajeno → 404 indistinguible. Revisa BOLA/BFLA por endpoint, el
  invariante del último administrador, invitaciones a otra organización y revocación en la
  siguiente petición.
- **PostgreSQL.** Consultas parametrizadas (sin SQL armado con texto), privilegios del rol de la
  app frente al de migraciones, transacciones que no dejen efectos parciales, `FOR UPDATE` donde
  hay carreras, datos sensibles en `JSONB` y en logs.
- **Almacenamiento (S-09, S-10, HU-B07, HU-B21).** Bucket privado, prefijo por organización,
  nombres aleatorios, URL temporal emitida solo tras verificar pertenencia, TTL corto,
  `Cache-Control: no-store`, sin URLs firmadas en logs. Si el alcance es S3: IAM de la instancia,
  Block Public Access, cifrado, política y CORS del bucket.
- **Carga de documentos (S-08, S-09).** Tipo real por contenido, límites de tamaño en Caddy y en
  la API, páginas limitadas en el worker, memoria y tiempo acotados, temporales eliminados en
  error. Para ZIP (no está en DAS ni HU): bombas de descompresión, profundidad, `../` y enlaces
  simbólicos, extracción aislada. Usa solo documentos sintéticos.
- **IA y evidencias (S-07, S-11, N-02, HU-B13 y B17 a B20).** Documento delimitado como datos,
  nunca concatenado a instrucciones; modelos sin herramientas; salida validada con Pydantic;
  citas comprobadas contra la fuente (rapidfuzz); cita no verificada, ambigüedad o baja confianza →
  revisión, nunca un «cumple» por defecto; la recomendación sale de reglas y el texto del modelo no
  puede cambiarla. Busca caminos donde una respuesta inválida, vacía o truncada termine como dato
  aceptado. Revisa reintentos, timeouts y topes de costo (S-08, HU-B26) frente a abuso.
- **Salidas hacia el frontend (S-11, S-12).** Texto de modelos y documentos tratado como texto
  plano; exportación a Excel con neutralización de `= + - @`.
- **Secretos y logs (S-06, S-14).** Nada de claves en código, imágenes, frontend o reportes; la
  service role key solo en backend; errores sin trazas ni rutas; logs con identificadores, no
  contenido de pliegos, hojas de vida ni tokens.
- **Infraestructura (S-01, S-02, S-13, HU-B27).** Solo Caddy publica 80/443; PostgreSQL y RabbitMQ
  en red interna (consola solo en loopback); credenciales largas; HTTPS y HSTS; CORS limitado;
  `FORWARDED_ALLOW_IPS`; usuario no root en la imagen; acciones de GitHub fijadas; permisos mínimos
  del `GITHUB_TOKEN`; manejo de la llave SSH y `known_hosts` en el CD; respaldo y restauración del
  volumen de PostgreSQL.

## 4. Clasificación de hallazgos

- **Evidencia:** `Demostrado` (prueba, reproducción local o línea de código que lo hace inevitable),
  `Potencial` (depende de configuración o de un supuesto plausible) o `No verificable` (requiere
  infraestructura, credenciales o documentos que no tienes). Nunca presentes un potencial como
  demostrado ni inventes resultados de pruebas.
- **Severidad:** `Crítica` (acceso entre organizaciones, bypass de autenticación, fuga de secretos,
  ejecución remota), `Alta` (escalada de rol, exposición de PDF, resultado falso aceptado como
  verificado, abuso de costos sin límite), `Media` (falta de defensa en profundidad, información en
  errores o logs), `Baja` (endurecimiento).

## 5. Entrega

Tabla ordenada por severidad:

`ID | Severidad | Evidencia | Ubicación (archivo:línea o endpoint) | S-/HU/OWASP | Impacto | Corrección propuesta | Prueba de regresión`

- La prueba de regresión debe ser concreta: nombre del test, archivo (`tests/` o
  `tests/integracion/` con dos organizaciones) y aserción. Los proveedores se simulan en
  `integrations/`; ninguna prueba gasta créditos.
- Añade «Controles verificados» (lo que está bien y con qué prueba) y «Pendiente de verificar fuera
  del repo» (grupo de seguridad AWS, HTTPS real, tope de gasto en Anthropic, Supabase real).
- Si una corrección implica una decisión de arquitectura, seguridad, costo o compatibilidad,
  preséntala como decisión pendiente de aprobación.
