# Agent Skills del backend

Abre Codex desde la raíz de `telettubiesplus-backend` o una de sus subcarpetas. En Codex CLI, usa `/skills` para comprobar las skills descubiertas y `$nombre-de-la-skill` para invocar una. Si una skill nueva no aparece, recarga el cliente y comprueba el directorio de trabajo. [Documentación oficial de Codex](https://learn.chatgpt.com/docs/build-skills#where-codex-loads-local-skills)

## Claude Code

Claude Code no lee `.agents/skills/` ni `AGENTS.md`. Descubre las skills del proyecto en
`.claude/skills/<nombre>/SKILL.md` y lee `CLAUDE.md`, que importa `AGENTS.md`. Abre Claude Code en
la raíz del backend, comprueba con `/skills` que cada nombre aparezca una sola vez e invoca con
`/<nombre> [alcance]`; Claude también las carga solo cuando la tarea coincide con su descripción.
[Documentación de skills](https://code.claude.com/docs/en/skills)

| Skill | Ubicación o mecanismo | Fuente | Estado | Ejemplo |
| --- | --- | --- | --- | --- |
| `criterio-security-review` | `.claude/skills/criterio-security-review/` | Propia | Creada (2026-11-12) | `/criterio-security-review HU-B21: URL temporal y matriz` |
| `criterio-architecture-review` | `.claude/skills/criterio-architecture-review/` | Propia | Creada (2026-11-12) | `/criterio-architecture-review HU-B09: reentregas e idempotencia` |
| Superpowers | Plugin de usuario (cada integrante) | [obra/superpowers](https://github.com/obra/superpowers), MIT | Autorizada (2026-11-12) | `/plugin marketplace add obra/superpowers-marketplace` y `/plugin install superpowers@superpowers-marketplace` |
| Supabase Postgres Best Practices | Plugin de usuario (cada integrante) | [supabase/agent-skills](https://github.com/supabase/agent-skills) | Autorizada (2026-11-12) | `/plugin marketplace add supabase/agent-skills` y `/plugin install postgres-best-practices@supabase-agent-skills` |
| Revisión de código | Skill incluida `code-review` y comando `/security-review` | Claude Code | Disponibles sin instalar | `/code-review` (alias `/review`), `/security-review` |
| `gh-fix-ci` | Solo Codex (`.agents/skills/gh-fix-ci`) | openai/skills, Apache-2.0 | Para Claude Code habría que copiarla a `.claude/skills/`: pendiente de decisión | `/gh-fix-ci` |
| Playwright | Frontend | — | No aplica al backend | — |

Las dos skills propias existen en `.claude/skills/` (Claude Code) y en `.agents/skills/` (Codex)
con **el mismo contenido**. Cambia ambas copias en el mismo commit; las dos usan solo los campos
`name` y `description`, compatibles con los dos clientes.

Configuración de proyecto de Claude Code, versionada en `.claude/`:

- `settings.json`: pide confirmación antes de `git commit`, `push`, `merge`, `rebase`, `reset`,
  `checkout`, `stash`, `gh pr` y del Compose de producción; bloquea `push --force` y la lectura de
  archivos `.env` reales. Cada integrante puede añadir excepciones personales en
  `.claude/settings.local.json` (no se versiona).
- `agents/revisor-independiente.md`: subagente de solo lectura que aplica las dos skills a un diff
  sin haber participado en él. Úsalo antes de pedir autorización para un commit o PR sensible.
- `CLAUDE.local.md` (no se versiona): instrucciones personales, por ejemplo la rama de trabajo.

Para actualizarlas: edita el `SKILL.md`, conserva `name` igual al nombre de la carpeta y una
`description` que diga cuándo usarla, y comprueba en una sesión nueva con `/skills` que se
descubre y con una invocación de prueba sobre un alcance pequeño.

## Revisiones de Criterio

Las skills personalizadas se mantienen en el repositorio y aplican los criterios del DAS y las HU a la arquitectura y al código de Criterio.

- [`criterio-security-review`](.agents/skills/criterio-security-review/SKILL.md) cubre seguridad de API, identidad, aislamiento, documentos, IA y operación. Ejemplo de invocación `$criterio-security-review Revisa el aislamiento de las URLs firmadas de HU-B21 y sus pruebas existentes.`
- [`criterio-architecture-review`](.agents/skills/criterio-architecture-review/SKILL.md) cubre módulos, procesamiento asíncrono, persistencia, recuperación y decisiones de arquitectura. Ejemplo de invocación `$criterio-architecture-review Evalúa la idempotencia y recuperación de HU-B09 frente al DAS.`

Ambas fueron creadas con el flujo de `$skill-creator`. Para actualizarlas, revisa el DAS y el backlog aplicables, edita el `SKILL.md` y ejecuta `quick_validate.py` de esa skill contra la carpeta editada.

## Skills generales

- [`gh-fix-ci`](.agents/skills/gh-fix-ci/SKILL.md) inspecciona checks de GitHub Actions de un PR usando `gh`. Ejemplo de invocación `$gh-fix-ci Diagnostica el check fallido de este PR y presenta un plan.` Se instaló desde `skills/.curated/gh-fix-ci` con el instalador oficial y una revisión fija. [OpenAI Skills, revisión instalada](https://github.com/openai/skills/tree/49f948faa9258a0c61caceaf225e179651397431/skills/.curated/gh-fix-ci)
- [Superpowers](.agents/skills/superpowers/using-superpowers/SKILL.md) aporta flujos de planificación, diagnóstico, revisión y verificación. El backend contiene las 15 skills y los recursos de la carpeta oficial `skills/` de la versión 7.0.2, junto con su licencia. Esta instalación de repositorio usa el descubrimiento nativo de Codex. Ejemplo de invocación `$systematic-debugging Investiga por qué falla el arranque local del worker.` Las operaciones de Git y los cambios de alcance siguen [AGENTS.md](AGENTS.md). [Repositorio oficial, revisión instalada](https://github.com/obra/superpowers/tree/22f0b463e7a5e579904dd6211a520321035526f7/skills)
- `supabase-postgres-best-practices` ya está disponible mediante el plugin de Supabase de esta sesión. Se reutiliza para revisar consultas, índices y conexiones de PostgreSQL, incluida la instancia propia de Criterio. Ejemplo de invocación `$supabase-postgres-best-practices Revisa los índices de las consultas filtradas por organización.` En otros clientes, comprueba primero si el plugin ya la ofrece. [Plugin oficial de Supabase](https://github.com/supabase/agent-skills)
- `playwright` se reserva para el frontend, que ya usa `@playwright/test` y tiene pruebas en `e2e/`. Si el equipo decide incorporar la skill, instálala en la raíz del frontend con el instalador oficial y una revisión verificada de `skills/.curated/playwright`. Ejemplo de invocación `$playwright Comprueba la carga y el progreso en el entorno de integración.` [Skill oficial de OpenAI](https://github.com/openai/skills/tree/main/skills/.curated/playwright)

## Instalación reproducible y actualizaciones

Al clonar el backend, Codex puede descubrir las skills incluidas en `.agents/skills/`. Conserva las licencias y los archivos que acompañan a cada `SKILL.md`.

- Para reinstalar `gh-fix-ci` en una copia que aún no la tenga, usa `python "<skill-installer>/scripts/install-skill-from-github.py" --repo openai/skills --ref 49f948faa9258a0c61caceaf225e179651397431 --path skills/.curated/gh-fix-ci --dest .agents/skills --method download`. Sustituye `<skill-installer>` por la ruta del instalador de Codex.
- La copia de `gh-fix-ci` resuelve `git` y `gh` a rutas absolutas y aplica el estilo Ruff del proyecto. Conserva esa adaptación al actualizar. Revisa los fragmentos de logs antes de citarlos para evitar divulgar información sensible.
- Para reinstalar Superpowers, descarga el archivo fuente de la revisión enlazada, extrae a un directorio temporal y copia `skills/` como `.agents/skills/superpowers/`, con `LICENSE` dentro de ese directorio.
- Ruff formatea los ejemplos Python de la referencia `writing-skills/references/anthropic-best-practices.md`. Comprueba el formato tras actualizar Superpowers.
- Para actualizar una skill externa, descarga la nueva revisión a un directorio temporal, revisa los cambios de instrucciones, permisos y scripts, sustituye la copia del proyecto y actualiza la revisión en este documento. Comprueba `/skills` desde la raíz del backend y confirma que el nombre aparezca una sola vez.
