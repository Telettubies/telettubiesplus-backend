# Convenciones del backend

Backend del analizador de pliegos (reto 12). La arquitectura de referencia es el DAS v0.3 y el
plan vivo está en `docs/PLAN.md`: léelo antes de implementar una historia y actualiza su tabla
de seguimiento al cerrarla.

## Comandos

```bash
uv sync                                                   # dependencias
docker compose -f docker-compose.dev.yml up -d --wait     # PostgreSQL + pgvector y RabbitMQ
uv run alembic upgrade head                               # esquema
uv run ruff check . && uv run ruff format --check .       # lint y formato
uv run mypy                                               # tipos (strict)
uv run pytest                                             # toda la suite
uv run pytest -m "not integracion"                        # solo unitarias, sin base de datos
uv run alembic revision --autogenerate -m "descripcion"   # migración tras cambiar un modelo
uv run alembic check                                      # modelos y migraciones coinciden
uv run analizador-cli exportar-openapi                    # regenera docs/openapi.json
```

Antes de cada commit deben pasar ruff, mypy y pytest. Si cambió un modelo, también
`alembic upgrade head && alembic check`. Si cambió un endpoint, regenera `docs/openapi.json`.

## Estructura

```
src/analizador/
├── api/            FastAPI: main, deps (auth y autorización), routers
├── contratos/      contratos de datos (P-18): conocimiento, extracción, verificación, dashboard
├── core/           configuración por entorno y logging
├── db/             motor, sesiones y migraciones Alembic
├── models/         ORM
├── integrations/   bordes externos: Supabase (auth y storage), Anthropic, NVIDIA
├── messaging/      topología de RabbitMQ y publicador
├── pipeline/       grafo LangGraph y etapas del análisis
├── worker/         consumidor de colas
└── cli.py
tests/              unitarias en la raíz; tests/integracion/ usa PostgreSQL real
```

## Reglas

- **Idioma.** Identificadores, comentarios, mensajes y commits en español. Cita el identificador
  del DAS (`RF-`, `ADR-`, `S-`, `P-`, `Q-`) cuando una decisión de código dependa de él.
- **Aislamiento (S-03).** Toda consulta filtra por `organizacion_id` del `Contexto`. Un recurso de
  otra organización responde 404, igual que si no existiera. La organización y el rol nunca se
  leen del cliente.
- **Configuración.** Todo valor ajustable va en `core/config.py` y en `.env.example`. Los nombres
  de modelos no tienen valor por defecto. Nunca se versionan secretos.
- **Proveedores externos.** Solo se llaman desde `integrations/`. El texto de documentos se envía
  a los modelos delimitado como datos, nunca concatenado a las instrucciones (S-07). Las salidas
  se validan contra un contrato Pydantic.
- **Errores.** `HTTPException` con mensaje para el usuario, sin trazas, claves ni rutas (S-14).
  Los logs registran identificadores, no contenido de documentos ni tokens.
- **Modelo de datos.** Enumeraciones con `str_enum` (`VARCHAR + CHECK`). Cada cambio de modelo
  lleva su migración reversible.
- **Pipeline.** Cada etapa persiste su resultado en PostgreSQL y es idempotente: repetirla no
  duplica filas. El estado del grafo lleva solo identificadores.
- **Pruebas.** Cada criterio de aceptación tiene prueba. Los proveedores se simulan; ninguna
  prueba gasta créditos. Las de integración llevan la marca `integracion` y prueban con dos
  organizaciones cuando hay datos.

## Flujo de trabajo

- Rama de trabajo: `sar`. Un PR hacia `develop` por historia; se fusiona con el CI en verde.
- `main` despliega en producción: no se hace push ni merge ahí desde el trabajo diario.
- Commits atómicos con Conventional Commits: `feat(HU-B07): guardar el pliego en el almacenamiento`.
- Nunca `push --force` ni reescritura de historia compartida.
