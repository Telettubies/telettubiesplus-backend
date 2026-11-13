"""Búsqueda vectorial en pgvector (C-06, ADR-013).

Es el único punto del código que consulta fragmentos por cercanía, y exige la organización:
ninguna búsqueda puede devolver fragmentos de otra (S-03, HU-B04). En el chat, que busca sobre
el pliego, exige además la licitación.
"""

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from analizador.models import Fragmento
from analizador.models.enums import OrigenFragmento


async def fragmentos_cercanos(
    session: AsyncSession,
    *,
    organizacion_id: uuid.UUID,
    embedding: Sequence[float],
    origen: OrigenFragmento,
    limite: int,
    licitacion_id: uuid.UUID | None = None,
    modelo: str | None = None,
) -> list[tuple[Fragmento, float]]:
    """Fragmentos más cercanos al vector, con su distancia coseno (0 = idéntico).

    `modelo` limita la búsqueda a los fragmentos vectorizados con ese modelo de embeddings:
    vectores de modelos distintos no son comparables ni tienen la misma dimensión (ADR-013).
    """
    if origen is OrigenFragmento.PLIEGO and licitacion_id is None:
        raise ValueError("La búsqueda sobre el pliego exige la licitación")

    distancia = Fragmento.embedding.cosine_distance(list(embedding)).label("distancia")
    consulta = select(Fragmento, distancia).where(
        Fragmento.organizacion_id == organizacion_id,
        Fragmento.origen == origen,
        Fragmento.embedding.is_not(None),
    )
    if origen is OrigenFragmento.PLIEGO:
        consulta = consulta.where(Fragmento.licitacion_id == licitacion_id)
    if modelo is not None:
        consulta = consulta.where(Fragmento.modelo_embedding == modelo)
    filas = await session.execute(consulta.order_by(distancia).limit(limite))
    return [(fragmento, float(valor)) for fragmento, valor in filas]
