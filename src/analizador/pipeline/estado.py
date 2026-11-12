"""Estado del grafo de análisis.

Solo identificadores: los datos (secciones, requisitos, resultados) se persisten en
PostgreSQL en cada etapa, de modo que el checkpoint (P-08) es pequeño y el dashboard
puede mostrar resultados parciales.
"""

from typing import TypedDict


class EstadoAnalisis(TypedDict):
    trabajo_id: str
    licitacion_id: str
    organizacion_id: str
