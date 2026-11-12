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
2. **Rol IAM** de la instancia con `s3:GetObject` / `s3:PutObject` sobre el bucket (sin claves en `.env`).
3. **IMDSv2 desde contenedores**: subir el *hop limit* a 2, o boto3 no obtiene las credenciales del rol:
   `aws ec2 modify-instance-metadata-options --instance-id <id> --http-put-response-hop-limit 2 --http-tokens required`
4. **IP elástica** y registro DNS A del dominio de la API hacia ella.
5. Instalar Docker y el plugin de Compose (Amazon Linux 2023: `sudo dnf install -y docker`, plugin
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
├── integrations/        # S3, Supabase Auth, Anthropic, NVIDIA
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
| Fuera por ahora | Seguridad por fila (P-22), cuotas (P-23), contratos de IA (P-18), CI/CD |

## Pendiente (stubs y TODO)

- Etapas del pipeline (`pipeline/etapas.py`): todas registran progreso pero no procesan nada.
- Carga e indexación de la base de conocimiento: bloqueadas por Q-05.
- Chat (RF-13) y observaciones (RF-15): responden 501.
- Índice vectorial: la columna `fragmentos.embedding` no tiene dimensión fija hasta decidir el
  modelo de embeddings (P-10).
- Política de reintentos automáticos: hoy un fallo va a la DLQ y se reintenta con
  `POST /api/licitaciones/{id}/reintentar`.
