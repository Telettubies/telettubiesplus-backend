"""Catálogo de etapas del análisis (sección 5.3 del DAS), en el orden en que se ejecutan.

Lo comparten el worker, que registra el avance, y la API, que lo muestra (HU-B08). No importa
nada del pipeline para que la API no cargue las dependencias de lectura de PDF ni de modelos.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Etapa:
    clave: str
    nombre: str
    # Porcentaje del análisis alcanzado al empezar la etapa.
    porcentaje: int


ETAPAS: tuple[Etapa, ...] = (
    Etapa("lectura_pdf", "Lectura del PDF", 5),
    Etapa("division_secciones", "División en secciones", 10),
    Etapa("filtrado", "Filtrado de secciones", 20),
    Etapa("extraccion", "Extracción de requisitos", 40),
    Etapa("localizacion", "Localización en el pliego", 50),
    Etapa("comprobacion_cobertura", "Comprobación de cobertura", 55),
    Etapa("recuperacion_evidencia", "Búsqueda de evidencia", 70),
    Etapa("verificacion", "Verificación de cumplimiento", 85),
    Etapa("validacion_citas", "Validación de citas", 90),
    Etapa("decision", "Brechas y recomendación", 95),
    Etapa("justificacion", "Justificación", 98),
    Etapa("cierre", "Cierre", 100),
)

POR_CLAVE: dict[str, Etapa] = {etapa.clave: etapa for etapa in ETAPAS}
