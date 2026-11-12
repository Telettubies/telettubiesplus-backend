---
name: criterio-architecture-review
description: Revisión de arquitectura y calidad del backend de Criterio (analizador de pliegos) frente al DAS, sus ADR, las HU y el código. Úsala al diseñar o revisar una HU, un cambio de módulos, contratos o API, el pipeline LangGraph y el worker de RabbitMQ, idempotencia y reintentos, consultas e índices de PostgreSQL/pgvector, rendimiento en la VM, observabilidad, pruebas, o al evaluar una ADR o un trade-off.
---

# Revisión de arquitectura de Criterio

Recomienda la solución más simple que cumpla los criterios de aceptación y las restricciones del
DAS. No propongas infraestructura, dependencias ni optimizaciones sin un requisito o una medición
que las justifique. Las convenciones del repositorio están en `CLAUDE.md`; no las repitas.

## 1. Contexto obligatorio

1. Alcance pedido por el usuario o, si falta, el diff frente a `develop` más los cambios sin commit.
2. `docs/PLAN.md`: decisiones del DAS que se siguen, decisiones del equipo, conflictos, supuestos y
   deuda técnica. Es la fuente del estado de cada HU.
3. DAS v0.3 (§5 pipeline, §6 vistas, §7 ADR y propuestas, §9 riesgos) e HU: en
   `../telettubiesplus-frontend/` o donde indique el equipo. Si faltan, dilo.
4. `docs/openapi.json`, `src/analizador/contratos/` y el código y las pruebas del flujo.

Clasifica cada afirmación: **implementado**, **configurado**, **probado** (con qué prueba),
**verificado en integración o despliegue** (con qué evidencia). Etiqueta las decisiones como
aceptadas (ADR), del equipo fuera del DAS, propuestas no aprobadas (P-xx) o pendientes (Q-xx).
Una propuesta no aprobada no es un requisito.

## 2. Restricciones que condicionan cualquier recomendación

- VM única de AWS: 2 vCPU, 8 GB, sin GPU; modelos solo por API (ADR-001); presupuesto limitado de
  Anthropic y créditos de NVIDIA (R-02, R-04).
- FastAPI + worker con el mismo paquete (ADR-003); RabbitMQ solo transporta identificadores y
  PostgreSQL es la única fuente de estado (ADR-006, ADR-015); LangGraph (ADR-007) con checkpointer
  (P-08); PostgreSQL + pgvector con volumen (ADR-009, ADR-013); Docker Compose (ADR-016).
- El pliego se recorre completo; la búsqueda vectorial no extrae requisitos (ADR-012). El modelo no
  hace cuentas ni decide la recomendación (P-03, P-05).
- Equipo de 4 personas y plazo corto: el costo de mantenimiento pesa tanto como la elegancia.

## 3. Ejes de revisión

- **Diseño.** SOLID, KISS, DRY y YAGNI aplicados con criterio: responsabilidades de `api/`,
  `contratos/`, `models/`, `db/`, `integrations/` (único borde con proveedores), `messaging/`,
  `pipeline/` y `worker/`. Señala acoplamientos que dificulten probar o cambiar un proveedor; no
  pidas abstracciones con una sola implementación sin razón.
- **Contratos.** Pydantic como fuente de verdad; OpenAPI regenerado; compatibilidad con el frontend
  (rutas, ids, códigos 202/409, nombres de campos). Un cambio incompatible requiere versión o
  coordinación explícita.
- **Procesamiento asíncrono.** Transacción y publicación (¿puede quedar una fila sin mensaje o un
  mensaje sin fila?), `ack` solo al terminar, reentregas, límite de intentos, mensajes fallidos,
  reanudación desde el checkpoint sin repetir llamadas pagadas.
- **Idempotencia y concurrencia.** Cada etapa repetida no duplica filas ni costo; bloqueos
  (`FOR UPDATE`) donde hay carreras; semáforos de concurrencia hacia proveedores; un análisis a la
  vez en la VM (R-09).
- **Backpressure y resiliencia.** Prefetch, cuotas por organización (HU-B26), límite de peticiones a
  NVIDIA, reintentos con espera y tope, timeouts por llamada y por trabajo, degradación segura
  (ante duda, revisión humana; ante filtro caído, extraer todo).
- **PostgreSQL y pgvector.** Filtro por organización en cada consulta; índices que respalden los
  filtros y órdenes frecuentes; N+1 y cargas ansiosas; tamaño de transacciones; pool de conexiones
  en API y worker; migraciones reversibles y `alembic check`; búsqueda exacta frente a índice
  vectorial (P-10 abierto); respaldo y restauración del volumen.
- **Rendimiento.** Pide o realiza una medición (tiempo por etapa, tokens y costo en `trabajo.detalle`,
  consultas lentas) antes de recomendar una optimización. Sin medición, márcala como hipótesis.
- **Observabilidad y operación.** Progreso por etapa, logs con identificadores sin contenido
  sensible, health checks útiles (`/health/ready`), errores accionables, costo por análisis
  (HU-B28).
- **Pruebas.** Cada criterio de aceptación con prueba; unitarias para reglas puras; integración
  con PostgreSQL y dos organizaciones; proveedores simulados en el borde; brechas de cobertura
  respecto a la HU.
- **Calidad (ISO/IEC 25010:2023).** Relaciona hallazgos con adecuación funcional, eficiencia de
  desempeño, compatibilidad, capacidad de interacción, fiabilidad, seguridad, mantenibilidad,
  flexibilidad y protección (safety). La priorización de atributos de calidad sigue pendiente en la
  §4 del DAS: no inventes metas numéricas; propón escenarios medibles como propuesta.

## 4. Evaluación de una ADR o trade-off

Para cada decisión: problema y fuerzas, restricciones (§2), alternativas (incluida «no hacer
nada»), beneficios, costos y riesgos, reversibilidad, impacto en HU y contratos, costo de
migración y la evidencia que la cambiaría. Recomienda una opción y deja la aprobación al equipo.

## 5. Entrega

1. Resumen de dos o tres frases.
2. Hallazgos: `ID | Eje | Estado (implementado/probado/verificado) | Evidencia (archivo:línea, prueba) | Riesgo | Recomendación proporcional | HU/ADR | Prioridad (Must/Should/Could)`.
3. Divergencias con el DAS y decisiones que requieren aprobación, separadas.
4. Próximos pasos ordenados con dependencias y posibilidad de trabajo en paralelo.

No modifiques código ni documentos salvo que la tarea lo autorice.
