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

## Calidad

```bash
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run pytest
```

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
| Fuera por ahora | Seguridad por fila (P-22), cuotas (P-23), contratos de IA (P-18), Dockerfiles y CI/CD |

## Pendiente (stubs y TODO)

- Etapas del pipeline (`pipeline/etapas.py`): todas registran progreso pero no procesan nada.
- Carga e indexación de la base de conocimiento: bloqueadas por Q-05.
- Chat (RF-13) y observaciones (RF-15): responden 501.
- Índice vectorial: la columna `fragmentos.embedding` no tiene dimensión fija hasta decidir el
  modelo de embeddings (P-10).
- Política de reintentos automáticos: hoy un fallo va a la DLQ y se reintenta con
  `POST /api/licitaciones/{id}/reintentar`.
