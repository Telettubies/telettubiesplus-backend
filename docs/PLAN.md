# Plan de implementación del backend

Fuentes: DAS v0.3 (`docs/das.pdf`) e historias de usuario (`docs/hu.pdf`), ambos del 2026-11-11.
Los identificadores `RF-`, `ADR-`, `P-`, `Q-`, `S-`, `R-` remiten al DAS; `HU-Bxx` al backlog.
Alcance de este plan: las **21 historias Must de backend**. Las Should y Could quedan para después.

## 1. Decisiones del DAS que se siguen

| Tema | Decisión | Origen |
| --- | --- | --- |
| Lenguaje y API | Python 3.12, FastAPI; API (C-02) y worker (C-03) comparten el paquete `analizador` | ADR-003 |
| Procesamiento asíncrono | RabbitMQ transporta solo identificadores; un mensaje a la vez | ADR-006, ADR-015, R-09 |
| Orquestación | LangGraph, etapas 1–11 de la sección 5.3, con checkpointer en PostgreSQL para reanudar | ADR-007, P-08 |
| Estado y progreso | PostgreSQL es la única fuente; el dashboard nunca consulta RabbitMQ | ADR-015 |
| Persistencia | PostgreSQL con volumen; vectores en pgvector dentro de la misma base | ADR-009, ADR-013 |
| Autenticación | Token de Supabase verificado en cada petición (firma, expiración, emisor, audiencia) | ADR-008, S-04 |
| Autorización | Organización y rol solo desde la tabla `miembros`; todo filtra por organización | ADR-014, S-03 |
| Modelos de IA | NVIDIA: filtro, embeddings, chat. Anthropic: extracción, verificación, redacción. Nombres por entorno | ADR-002, P-19 |
| Extracción | Se recorre todo el pliego sección por sección; la búsqueda vectorial no extrae requisitos | ADR-012 |
| Despliegue | Docker Compose, mismo entorno en local y en la VM; solo el proxy publica puertos | ADR-016, S-01 |
| Seguridad mínima | S-01 a S-08: secretos por entorno, documentos tratados como datos, límites de uso | 6.6 |
| Taxonomía y estados | Los del reto: habilitante, técnico, experiencia, plazo; cumple, cumple parcialmente, no cumple | RF-01, RF-04 |

Convenciones heredadas del andamiaje: SQLAlchemy 2 asíncrono, Alembic, enumeraciones como
`VARCHAR + CHECK`, `uv` para dependencias, `ruff` y `mypy --strict`, identificadores en español.

## 2. Decisiones tomadas con el responsable del backend (2026-11-12)

| Tema | Decisión | Relación con el DAS |
| --- | --- | --- |
| Flujo de ramas | Trabajo en `sar`; un PR `sar → develop` por historia, fusionado cuando el CI pasa. `main` no se toca (dispara el despliegue) | — |
| Contratos de datos | Se definen en `src/analizador/contratos/` con un JSON sintético de ejemplo | P-18 (no aprobada) |
| Lectura de PDF | PyMuPDF (texto, coordenadas, render) y pdfplumber (tablas) | Etapa 1 [P]; R-13 por la licencia AGPL |
| OCR | Tesseract en español; Claude con visión como respaldo. Se implementa dentro de HU-B10 | P-09 (no aprobada) |
| Reglas de recomendación | Estricta (ver sección 5) | P-05, Q-04 (no aprobadas) |
| Almacenamiento de PDF | Supabase Storage, bucket privado, por API REST | **Reemplaza ADR-005 (AWS S3)** |

## 3. Orden de implementación y seguimiento

Estados: `pendiente`, `en curso`, `hecha`. Las filas `T-` son tareas técnicas transversales.

| # | Ítem | Descripción | Estado | Rama | Commit | PR |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | T-00 | Plan, convenciones, pruebas de integración con PostgreSQL, errores, OpenAPI exportado | hecha | sar | c553b72…f9c6487 | #6 |
| 2 | HU-B01 | Verificar identidad y resolver organización y rol | hecha | sar | facdfab | #7 |
| 3 | T-01 | Migrar el almacenamiento de S3 a Supabase Storage | hecha | sar | acb433d, 551d0b0 | #8 |
| 4 | HU-B04 | Aislar los datos de cada organización | pendiente | sar | | |
| 5 | HU-B07 | Cargar un pliego para análisis | pendiente | sar | | |
| 6 | HU-B08 | Consultar el progreso de un análisis | pendiente | sar | | |
| 7 | HU-B09 | Orquestar el pipeline y tolerar fallos | pendiente | sar | | |
| 8 | HU-B26 | Limitar el uso para proteger créditos y presupuesto | pendiente | sar | | |
| 9 | HU-B05 | Cargar la base de conocimiento estructurada | pendiente | sar | | |
| 10 | HU-B06 | Indexar los documentos de la empresa | pendiente | sar | | |
| 11 | HU-B10 | Leer el texto del pliego, incluidas páginas escaneadas | pendiente | sar | | |
| 12 | HU-B11 | Dividir el pliego en secciones con su numeral | pendiente | sar | | |
| 13 | HU-B12 | Filtrar secciones sin requisitos | pendiente | sar | | |
| 14 | HU-B13 | Extraer y atomizar requisitos | pendiente | sar | | |
| 15 | HU-B14 | Comprobar la cobertura del pliego | pendiente | sar | | |
| 16 | HU-B15 | Localizar cada requisito en el PDF | pendiente | sar | | |
| 17 | HU-B16 | Recuperar evidencia de la base de conocimiento | pendiente | sar | | |
| 18 | HU-B17 | Verificar el cumplimiento de cada ítem | pendiente | sar | | |
| 19 | HU-B18 | Validar que las citas existen en la fuente | pendiente | sar | | |
| 20 | HU-B20 | Enviar a revisión los ítems ambiguos o dudosos | pendiente | sar | | |
| 21 | HU-B19 | Calcular brechas, recomendación y justificación | pendiente | sar | | |
| 22 | HU-B21 | Entregar el resultado y el acceso al PDF | pendiente | sar | | |
| 23 | HU-B27 | Desplegar de forma segura en la VM | pendiente | sar | | |

Fuera de este plan: HU-B02, B03, B22, B23, B24, B25 (Should) y HU-B28, B29 (Could). Parte de
su código ya existe en el andamiaje; solo se toca para garantizar el aislamiento (HU-B04).

Dependencias que fijan el orden: HU-B01 antes de todo lo protegido; T-01 antes de HU-B04 y
HU-B07 (rutas y URL del almacenamiento); HU-B26 antes de las historias que llaman a NVIDIA;
HU-B05 y B06 antes de HU-B16; HU-B10 → B11 → B12 → B13 → B14 → B15 en el orden del pipeline;
HU-B18 y B20 antes de HU-B19 porque la recomendación depende de qué ítems quedan en revisión.

## 4. Conflictos entre las historias y el DAS

| Conflicto | Resolución |
| --- | --- |
| **Almacenamiento.** ADR-005 y HU-B27 (criterio 7: rol IAM de la VM) hablan de AWS S3; el equipo ya usa Supabase Storage | Se sigue la decisión del equipo. El criterio 7 de HU-B27 se sustituye por: la clave de servicio de Supabase solo en el backend y por variable de entorno (S-06). ADR-005 debe actualizarse en el DAS v0.4 |
| **Estado "revisión".** HU-B17 (criterio 1) lo lista como cuarto estado; RF-04 y Q-17 mantienen tres | Se sigue el DAS: la base guarda los tres estados más `requiere_revision` y `motivo_revision`. La API expone `estado: "revision"` cuando aplica y el estado propuesto aparte |
| **Límite de páginas.** HU-B07 (criterio 3) pide rechazar al subir; S-09 y HU-B10 exigen abrir el PDF solo en el worker | Se sigue el DAS: el tamaño se rechaza al subir; el exceso de páginas falla en la etapa 1 con un mensaje que indica el límite |
| **Duplicados por hash.** HU-B07 (criterio 4) depende de P-16, no aprobada, y dice omitirlo en ese caso | Se implementa solo el hash del PDF por organización, porque HU-F05 (Must) lo espera y protege el presupuesto (RC-02). No se implementa la caché de fragmentos del pliego |

## 5. Supuestos

1. **Reglas de recomendación (Q-04).** En este orden: (a) hay ítems en revisión, secciones sin
   procesar o ningún ítem extraído → `condicionada`; (b) algún ítem habilitante en *no cumple* o
   *cumple parcialmente* → `no_presentarse`; (c) cualquier otra brecha → `presentarse_con_riesgo`;
   (d) sin brechas → `presentarse`.
2. **Selección de organización.** `X-Organizacion-Id` solo elige entre membresías ya verificadas
   en PostgreSQL (ADR-014 admite varias organizaciones por usuario). Nunca concede acceso.
3. **Vectores.** La columna de embeddings no fija dimensión y la búsqueda es exacta, sin índice,
   porque el modelo sigue abierto (P-10). Cada fragmento guarda el modelo con el que se vectorizó.
4. **Base de conocimiento.** Solo carga estructurada en JSON (Q-05). No se suben hojas de vida en PDF.
5. **Modelos.** Todos los nombres vienen del entorno, sin valores por defecto en el código (P-10, P-19).
6. **Valores sin decidir** (P-23, Q-12, Q-14): tamaño y páginas máximas, cuotas, SMMLV y umbrales
   son configuración obligatoria o con valor documentado en `.env.example`; no se fijan en el código.
7. **Pruebas.** Ninguna prueba llama a Anthropic, NVIDIA ni Supabase: se simulan en el borde de
   `integrations/`. Las pruebas de integración usan PostgreSQL con pgvector real.
8. **Formato de error.** Se conserva el de FastAPI (`{"detail": ...}`); los errores no controlados
   responden 500 con un mensaje genérico.
9. **Documentos de referencia.** `docs/das.pdf` y `docs/hu.pdf` no se versionan en este repositorio.

## 6. Ambigüedades detectadas

- **Creación de organizaciones.** El DAS solo describe cómo un administrador invita revisores. Se
  mantiene el comando `analizador-cli crear-organizacion`.
- **Campos comparables por código (P-03).** El DAS no enumera los indicadores. Se define un
  conjunto cerrado en el contrato de extracción; lo que no encaje se verifica como cualitativo.
- **Subsanabilidad (Q-01, RF-20).** Sin criterios: las brechas no se clasifican como subsanables.
- **Pliego de demostración (Q-11).** No está elegido. HU-B11 y HU-B15 se prueban con un PDF
  sintético; la medición sobre un pliego real de SECOP queda pendiente.
- **Reranker (P-11).** No aprobado: queda implementado pero apagado por configuración.

## 7. Deuda técnica

Se completa al cerrar cada historia.
