# Base empresarial documental

Ampliación del flujo RF-03 / Q-05 solicitada por el responsable del proyecto.
Conserva la importación JSON y utiliza la misma base para comparar licitaciones.

## Uso

1. Un administrador carga un PDF desde la base de conocimiento del frontend.
2. La API valida firma y tamaño, guarda el objeto en el bucket privado de su
   organización y encola un trabajo. Cargar el mismo PDF devuelve el mismo documento.
3. El worker lee texto/tablas y aplica OCR cuando corresponda. La visión es opcional
   y usa la configuración OCR existente. Extrae candidatos por página con el modelo
   `ANTHROPIC_MODEL_EXTRACCION`, sin incorporarlos automáticamente a la base activa.
4. El administrador abre el PDF, revisa la cita y página, corrige o descarta registros
   y confirma los posibles reemplazos. Los campos ausentes no se inventan; la
   aprobación exige que la base resultante cumpla el contrato estructurado existente.
5. La indexación calcula/reutiliza embeddings antes de publicar la propuesta. Solo
   después cambia la versión activa. Un fallo mantiene datos e índice anteriores.
6. El análisis fija una versión empresarial para que la evidencia semántica y los
   datos numéricos correspondan a la misma base durante el procesamiento.

Los registros con el mismo ID, año financiero o nombre se revisan como reemplazos.
La importación JSON sigue siendo una sustitución completa, pero ahora se publica
después de indexar. La identificación general de la empresa sigue siendo obligatoria;
si el primer PDF no contiene esos datos, deben completarse durante la revisión.

## Recuperación e historial

- La lectura y los candidatos extraídos se guardan por página para reintentar sin
  repetir las páginas ya extraídas. Fallos de cola y del worker son visibles.
- Las propuestas que quedaron obsoletas por otra carga no se publican.
- Una revisión abierta no puede sustituir una versión cambiada por otro usuario:
  la API devuelve 409 y exige revisar contra la versión actual.
- Se puede rechazar un documento pendiente, o retirar los datos de uno aprobado.
  El retiro también se publica después de indexar. No borra PDFs ni fuentes
  históricas referenciadas por análisis anteriores.
- Los datos generales de empresa deben sustituirse por otra fuente antes de retirar
  el documento que los respalda. Las fuentes JSON sin PDF siguen siendo válidas.
- Desde la evidencia puede solicitarse una URL privada temporal del documento
  original, con la página asociada. Las URLs se devuelven con `Cache-Control: no-store`.
- El índice del chat se puede recuperar por separado, sin repetir el análisis.
- Los mensajes duplicados se protegen mediante un advisory lock transaccional por
  trabajo; el prefetch global limita el consumo en el canal del worker.

## Configuración

- `MAX_DOCUMENTOS_EMPRESA`: documentos conservados por organización; predeterminado 100.
- `DOCUMENTO_EMPRESA_MAX_PAGINAS`: páginas por PDF empresarial; predeterminado 50.
- `MAX_PDF_MB`: límite de tamaño compartido con la carga de pliegos.
- Modelos, claves y límites de OCR/NVIDIA/Anthropic conservan sus variables existentes.
  No se añadieron nombres de modelos ni dominios de producción fijos.

El frontend consulta los límites del backend. El worker procesa los documentos
mediante la cola de indexación existente, con una operación identificada en el trabajo.
La API y el worker deben usar la misma versión de imagen.

Un HTTP 500 de NVIDIA puede seguir ocurriendo: no es evidencia suficiente para
cambiar de modelo o atribuir el problema al tamaño del lote. Se registran estado y
request ID cuando estén disponibles, sin texto documental ni claves. Se validan
índices, cantidad, dimensión consistente y finitud de los vectores antes de guardarlos.

## Migración y compatibilidad

La revisión Alembic `362f8aee173c` agrega `documentos_empresa` y
`cambios_conocimiento`. No requiere convertir las fuentes existentes: se tratan como
versión `legacy` hasta la primera activación nueva. Las versiones posteriores se
conservan con sus fuentes y fragmentos; el orden de propuestas usa una secuencia de
PostgreSQL, no el reloj de la máquina.

Antes de usar el flujo nuevo hay que aplicar `alembic upgrade head` con la nueva
imagen y actualizar API y worker. El servicio `migrate` del Compose ya cumple esa
responsabilidad. No se ejecutó esta migración en producción durante la implementación.

La migración se comprobó con upgrade, downgrade y `alembic check` en una base
desechable. Un rollback de aplicación después de cargar versiones reales requiere
planificar la restauración de datos: el código antiguo no conoce la selección de
versiones y no debe consultar directamente todas las fuentes históricas. No ejecutar
un downgrade en producción sin respaldo y revisión del historial que se conservará.

## Verificación

Las pruebas cubren PDFs digitales y escaneados, citas y datos inválidos, aprobación
manual, aislamiento por organización, reemplazo fallido, retiro con conservación de
evidencia, entregas simultáneas y un recorrido de todos los nodos reales hasta matriz
y exportación. El almacenamiento y los proveedores de IA se simulan; PostgreSQL y
pgvector se ejecutan en un contenedor desechable. Estas pruebas no certifican la
disponibilidad actual de los modelos externos.

El frontend incluye pruebas de carga, permisos y combinación de registros, además
de la comprobación del contrato OpenAPI y de tipos. La compilación mantiene el aviso
existente sobre tamaño de algunos bundles.

## Indexación automática de PDF

Los trabajos de documento intentan publicar e indexar los candidatos después de extraerlos,
si la base combinada cumple el contrato `BaseConocimiento`. No se rellenan datos obligatorios
ausentes: los documentos incompletos permanecen en revisión y conservan la aprobación manual.
Un documento en revisión puede volver a encolarse con
`POST /api/conocimiento/documentos/{id}/reintentar`. Las publicaciones automáticas se
serializan por organización; un fallo de embeddings conserva la base anterior. La
publicación automática no registra un aprobador humano.
