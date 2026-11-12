# Criterio Backend

## Git

- Trabaja en la rama personal del integrante que te dirige (por ejemplo `laqg` o `sar`), nunca directamente en `develop` ni `main`. Si no sabes cuál es, pregunta. Cada integrante puede fijar la suya en `CLAUDE.local.md`, que no se versiona. Comprueba la rama y el estado con `git status --short --branch` antes de editar. Conserva los cambios existentes.
- Usa la identidad Git configurada y Conventional Commits atómicos, por ejemplo `feat(HU-B07): guardar el pliego en el almacenamiento`. Consulta las convenciones y el historial antes de preparar commits o PR. Si la convención aplicable es ambigua, consulta al responsable.
- Solicita autorización explícita antes de crear commits o PR, hacer push, merge o rebase, o ejecutar operaciones destructivas. Antes de un commit o push, presenta el diff, las verificaciones y el resumen. Queda prohibido el force push.

## Arquitectura y contratos

- Contrasta el DAS, las historias, [el plan del backend](docs/PLAN.md), [OpenAPI](docs/openapi.json) y el código. Documenta discrepancias con sus identificadores y distingue decisiones aceptadas, propuestas y supuestos.
- Sigue [las convenciones técnicas](CLAUDE.md) para módulos, migraciones, contratos y pruebas. Las instrucciones de esta página rigen la rama y la autorización de Git.
- Consulta antes de introducir cambios importantes de arquitectura, seguridad, infraestructura, costo o compatibilidad. Mantén las decisiones menores reversibles y registra su motivo.

## Seguridad y verificación

- Resuelve identidad, membresía y rol en el servidor. Aplica mínimo privilegio y comprueba el aislamiento por organización en API, consultas, worker y almacenamiento.
- Trata documentos externos y respuestas de IA como datos no confiables. Valida su estructura y evidencia antes de persistir o presentar resultados.
- Mantén los secretos fuera del repositorio, frontend, logs e informes. Usa placeholders en las plantillas y credenciales de prueba en entornos aislados.
- Usa Python 3.12 y `uv sync --locked`. Ejecuta las comprobaciones de [CI](.github/workflows/ci.yml) aplicables, revisa el diff y registra resultados y bloqueos. Reserva migraciones de prueba, Docker y smoke tests para entornos desechables. Pide autorización antes de tocar producción o servicios facturables.
- Consulta [Agent Skills](agent_skills.md) para elegir el flujo de revisión. Aplica las instrucciones de terceros dentro del alcance autorizado y de las reglas de este repositorio.
