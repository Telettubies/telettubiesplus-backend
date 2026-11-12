# telettubiesplus-backend

Backend del **analizador de pliegos y matriz de cobertura** (reto 12). Contiene la API (C-02) y
el worker de análisis (C-03), que comparten el paquete `analizador`. La arquitectura de referencia
es el DAS v0.3; los identificadores del código (RF-, ADR-, P-, S-, Q-) apuntan a ese documento.

## Requisitos

- [uv](https://docs.astral.sh/uv/) (instala Python 3.12 automáticamente)
- Docker, para PostgreSQL + pgvector y RabbitMQ en local

## Puesta en marcha local

```bash
uv sync                                   # dependencias (incluye las de desarrollo)
cp .env.example .env                      # completar contraseñas y credenciales
docker compose -f docker-compose.dev.yml up -d --wait
uv run alembic upgrade head               # crea el esquema
uv run pre-commit install                 # hooks de ruff + mypy

uv run analizador-api                     # API en http://localhost:8000 (docs en /docs)
uv run analizador-worker                  # worker (en otra terminal)
```

Alta de la primera organización y su administrador (el usuario debe existir en Supabase):

```bash
uv run analizador-cli crear-organizacion --nombre "Mi empresa" \
  --admin-usuario-id <uuid-de-supabase> --admin-email admin@empresa.co
```

## Despliegue en producción (EC2)

Una sola imagen ([Dockerfile](Dockerfile)) corre la API, el worker y las migraciones.
[docker-compose.prod.yml](docker-compose.prod.yml) levanta PostgreSQL + pgvector, RabbitMQ,
`migrate` (Alembic, una vez por despliegue), `api`, `worker` y Caddy como proxy con HTTPS
automático ([deploy/Caddyfile](deploy/Caddyfile)). Solo Caddy publica puertos (80/443).

Preparación de la instancia (una vez):

1. **Security group**: entrada 80, 443 y 22. El CD entra por SSH desde runners de GitHub (IPs variables),
   así que 22 debe quedar abierto con acceso solo por llave. No abrir 5432, 5672 ni 15672.
2. **Supabase Storage**: crear un bucket **privado** y poner su nombre en `SUPABASE_STORAGE_BUCKET`.
   El backend lo usa con `SUPABASE_SERVICE_ROLE_KEY`; los PDF solo salen por URL firmadas de pocos
   minutos (`STORAGE_URL_TTL_SECONDS`). No hacen falta credenciales de AWS ni rol IAM.
3. **IP elástica** y registro DNS A del dominio de la API hacia ella.
4. Instalar Docker y el plugin de Compose (Amazon Linux 2023: `sudo dnf install -y docker`, plugin
   `docker-compose-plugin` o el binario oficial; `sudo systemctl enable --now docker`).

Primer despliegue en la instancia: crear la carpeta y su `.env` (el resto lo copia el CD):

```bash
mkdir -p ~/analizador && cd ~/analizador
# copiar aquí el contenido de .env.prod.example como .env y completar secretos
chmod 600 .env                     # contraseñas: openssl rand -hex 24
```

## CI/CD (GitHub Actions)

| Workflow | Disparo | Qué hace |
| --- | --- | --- |
| [ci.yml](.github/workflows/ci.yml) | PR hacia `develop` | ruff (lint + formato), mypy, pytest, `alembic upgrade/check/downgrade` contra pgvector, build de la imagen y prueba de humo del Compose de producción |
| [cd.yml](.github/workflows/cd.yml) | Merge (push) en `main` o manual | Publica la imagen en GHCR (`:<sha>` y `:latest`), copia el Compose a la EC2 por SSH, `pull` + `up --wait` (migraciones incluidas) y verifica `/health/ready` |

Configuración en GitHub (*Settings → Secrets and variables → Actions*):

| Tipo | Nombre | Valor |
| --- | --- | --- |
| Secreto | `EC2_HOST` | IP elástica de la instancia |
| Secreto | `EC2_USUARIO` | Usuario SSH (`ec2-user` en Amazon Linux, `ubuntu` en Ubuntu) |
| Secreto | `EC2_LLAVE_SSH` | Contenido completo del `.pem` |
| Variable (opcional) | `EC2_DIRECTORIO` | Carpeta del despliegue en la instancia (defecto `analizador`) |
| Variable (opcional) | `PLATAFORMA` | `linux/arm64` si la instancia es Graviton (defecto `linux/amd64`) |
| Variable (opcional) | `API_URL` | `https://api.ejemplo.com`, para verificar desde fuera tras desplegar |

Los secretos de la aplicación no pasan por GitHub: viven solo en el `.env` de la instancia. El job de
despliegue usa el environment `produccion` (se puede exigir aprobación manual en *Settings →
Environments*). El security group debe permitir SSH (22) desde los runners de GitHub.

Operación manual en la instancia: `docker compose -f docker-compose.prod.yml logs -f api worker`.

La consola de RabbitMQ queda en `127.0.0.1:15672` de la instancia: `ssh -L 15672:127.0.0.1:15672 ec2-user@<ip>`.

## Calidad

```bash
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run pytest                        # unitarias + integración
uv run pytest -m "not integracion"   # solo unitarias, sin base de datos
```

Las pruebas de integración (`tests/integracion/`) usan PostgreSQL con pgvector real y simulan
Supabase, Anthropic y NVIDIA: no gastan créditos. En local toman las credenciales de
`POSTGRES_USER` / `POSTGRES_PASSWORD` del `.env` y crean la base `analizador_test` en el
PostgreSQL del Compose de desarrollo; si no está arriba, se omiten. En el CI la base llega por
`TEST_DATABASE_URL` y, si falta, fallan.

El contrato de la API para el frontend está en [docs/openapi.json](docs/openapi.json); se
regenera con `uv run analizador-cli exportar-openapi` y una prueba falla si queda desactualizado.
El plan de implementación y su seguimiento están en [docs/PLAN.md](docs/PLAN.md).

## Estructura

```
src/analizador/
├── api/                 # FastAPI (C-02): main, dependencias de auth y routers
├── core/                # configuración por entorno y logging
├── db/                  # motor/sesiones SQLAlchemy y migraciones Alembic
├── models/              # ORM (sección 6.3 del DAS)
├── schemas.py           # entrada/salida de la API
├── integrations/        # Supabase (Auth y Storage), Anthropic, NVIDIA
├── messaging/           # topología de RabbitMQ y publicador
├── pipeline/            # grafo LangGraph y etapas 1–11 (stubs)
├── worker/              # consumidor de colas (C-03)
└── cli.py               # comandos de administración
```

## Decisiones aplicadas en este arquetipo

| Tema | Decisión |
| --- | --- |
| Python / dependencias | Python 3.12 con uv |
| Base de datos | SQLAlchemy 2 async + Alembic; enums como VARCHAR + CHECK |
| Cola | aio-pika; colas `analisis`, `indexacion`, `reevaluacion` + DLQ `fallidos` (P-15) |
| Pipeline | LangGraph con checkpointer en PostgreSQL para reanudar (P-08) |
| Modelos de IA | Nombres por variable de entorno (P-19); sin valores por defecto mientras P-10 siga abierta |
| Taxonomía y estados | Los del reto (RF-01, RF-04) hasta resolver Q-16 / Q-17 |
| Autenticación | JWT de Supabase verificado con JWKS (claves asimétricas) |
| Autorización | Organización y rol desde `miembros` (ADR-014); `X-Organizacion-Id` si el usuario tiene varias |
| Almacenamiento | Supabase Storage (bucket privado, API REST) en lugar de AWS S3; ver `docs/PLAN.md` |
| Fuera por ahora | Seguridad por fila (P-22), cuotas (P-23), contratos de IA (P-18), CI/CD |

## Pendiente (stubs y TODO)

- Etapas 4 a 10 del pipeline (`pipeline/etapas.py`): registran progreso pero aún no procesan.
- Chat (RF-13) y observaciones (RF-15): responden 501.
- Índice vectorial: la columna `fragmentos.embedding` no tiene dimensión fija ni índice (búsqueda
  exacta) hasta decidir el modelo de embeddings (P-10). Cambiar de modelo exige reindexar.

## Fallos y reintentos del análisis (HU-B09)

- El worker toma un mensaje a la vez y lo confirma a RabbitMQ solo cuando el trabajo termina.
- Si una etapa falla, el trabajo queda `fallido` con la etapa en que falló y un mensaje para el
  usuario; el mensaje va a la cola `analizador.fallidos` y **no se reintenta solo**.
- `POST /api/licitaciones/{id}/reintentar` lo vuelve a encolar: con el checkpointer (P-08) el
  análisis continúa desde la última etapa completada, sin repetir llamadas ya pagadas.
- Si el worker se detiene a mitad, RabbitMQ reentrega el mensaje y el análisis se reanuda. Tras
  `PIPELINE_MAX_INTENTOS` entregas, o al superar `PIPELINE_TIMEOUT_SEGUNDOS`, el trabajo se da
  por fallido.

## Límites de uso (HU-B26)

| Límite | Variable | Dónde se aplica |
| --- | --- | --- |
| Tamaño del PDF | `MAX_PDF_MB` | Al subir (413) |
| Páginas del PDF | `MAX_PDF_PAGINAS` | En el worker, al leer el PDF |
| Análisis por organización en 24 h | `CUOTA_ANALISIS_DIARIA` | Al subir (429); un PDF duplicado no cuenta |
| Preguntas al chat por organización en 24 h | `CUOTA_CHAT_DIARIA`, `CHAT_MAX_CARACTERES` | En el chat (429 / 422) |
| Peticiones por minuto a NVIDIA | `NVIDIA_PETICIONES_POR_MINUTO`, `NVIDIA_MAX_REINTENTOS` | En cada llamada, con reintento y espera ante 429 |

`GET /api/limites` devuelve los límites vigentes y el consumo de la organización. Los valores por
defecto son provisionales (P-23 no fija cifras).

**Tope de gasto en Anthropic (tarea manual, no de código):** en la consola de Anthropic,
*Settings → Limits*, fijar un límite de gasto mensual acorde al presupuesto de 40 € (RC-02) y una
alerta de consumo. Es la última barrera si fallan las cuotas.

## Base de conocimiento (HU-B05)

Un administrador carga un único archivo JSON con `POST /api/conocimiento` (campo `archivo`). El
contrato está en `src/analizador/contratos/conocimiento.py` y hay un ejemplo sintético completo
en [docs/ejemplos/base_conocimiento.json](docs/ejemplos/base_conocimiento.json).

- El archivo se valida entero: si algo falla responde 422 con **todos** los errores, cada uno con
  su campo (`contratos[2].fecha_fin`), su ubicación y un mensaje; no se guarda nada.
- Cada carga reemplaza a la anterior por completo y en una sola transacción.
- Tras guardar se encola la indexación (HU-B06): un fragmento por contrato, perfil, experiencia,
  certificación, capacidad y año financiero, vectorizado por lotes con `NVIDIA_MODEL_EMBEDDINGS`. Un
  texto ya vectorizado no se reenvía y el índice se reemplaza completo o no se toca.
- `GET /api/conocimiento` muestra el resumen y el estado de la indexación, y
  `POST /api/conocimiento/indexacion` la vuelve a lanzar si falló.

## Lectura del pliego y OCR (HU-B10)

La etapa 1 lee el PDF página por página, solo en el worker:

1. **Página digital:** texto en orden de lectura con PyMuPDF; las tablas con líneas se extraen con
   pdfplumber y se insertan en su sitio como filas `celda | celda`.
2. **Página escaneada** (sin texto extraíble y con imágenes): Tesseract en español. Se guardan las
   cajas de cada palabra para poder resaltar el requisito en el visor.
3. **Tesseract no logra texto legible:** Claude con visión transcribe la página, si
   `ANTHROPIC_MODEL_OCR` está configurado y sin pasar de `OCR_VISION_MAX_PAGINAS` por pliego.
4. **Tampoco:** la página queda marcada como no leída y el trabajo lo informa en `detalle`.

El pliego que supera `MAX_PDF_PAGINAS` falla en esta etapa con un mensaje que indica el límite.
La imagen Docker incluye `tesseract-ocr` y `tesseract-ocr-spa`. En local, sin el idioma español
instalado, la prueba de Tesseract real se omite (en el CI se ejecuta).

PyMuPDF tiene licencia AGPL (riesgo R-13 del DAS si el proyecto continúa tras la hackatón).

## División en secciones (HU-B11)

La etapa 2 parte el texto del pliego en secciones por reglas, sin modelos
(`pipeline/secciones.py`). Abre sección cada numeral jerárquico al inicio de línea (`3.2.1
Capacidad jurídica`, `4. CRONOGRAMA`) y cada `CAPÍTULO`, `TÍTULO`, `SECCIÓN`, `ANEXO`, `FORMATO`
o `APÉNDICE` numerado. No abren sección las entradas de la tabla de contenido, los ítems de una
lista numerada ni las cifras al inicio de una línea. El texto anterior al primer encabezado
forma la sección «Preámbulo».

Las secciones son tramos contiguos del texto: su concatenación reproduce el pliego exacto, y la
etapa lo comprueba por longitud antes de guardarlas. Una sección más larga que
`SECCION_MAX_CARACTERES` se parte en tramos con el mismo numeral.

## Filtro de secciones (HU-B12)

La etapa 3 pregunta a un modelo de NVIDIA (`NVIDIA_MODEL_FILTRO`), por lotes, qué secciones no
contienen requisitos, para no gastar presupuesto de Claude en antecedentes, glosarios o minutas.
El filtro solo puede reducir costo, nunca cobertura:

- Una sección se descarta únicamente con un "no" explícito, válido y con motivo. El motivo queda
  guardado en `secciones.motivo_filtro`.
- Duda, respuesta inválida, sección sin respuesta, modelo sin configurar o NVIDIA caído: la
  sección pasa a extracción.
- Aunque el modelo la descarte, una sección que mencione causales de rechazo o requisitos
  habilitantes pasa a extracción.
