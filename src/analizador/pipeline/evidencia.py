"""Etapa 6: recuperar evidencia de la base de conocimiento para cada ítem (RF-03, HU-B16).

Para cada ítem cualitativo se vectoriza su descripción como consulta y se buscan en pgvector
los fragmentos más cercanos de la organización. Si el reranker está activo, reordena los
candidatos; si no, se usan los primeros de la búsqueda. Los ítems numéricos no pasan por aquí:
se verifican por código contra los datos estructurados (P-03).

- La búsqueda siempre filtra por la organización (S-03) y por el modelo de embeddings vigente.
- Sin fragmentos relevantes el ítem sigue a verificación sin evidencia.
- El resultado se guarda en `items.candidatos` con el texto del fragmento, para que el estado
  de cada ítem conserve exactamente qué evidencia lo produjo.
"""

import logging
import uuid
from typing import Any

from sqlalchemy import func, select

from analizador.core.config import get_settings
from analizador.db.busqueda import fragmentos_cercanos
from analizador.db.session import get_sessionmaker
from analizador.integrations import nvidia
from analizador.models import Fragmento, FuenteConocimiento, Item, Requisito
from analizador.models.enums import OrigenFragmento, TipoVerificacion
from analizador.pipeline import progreso
from analizador.pipeline.errores import AnalisisError
from analizador.pipeline.estado import EstadoAnalisis

logger = logging.getLogger(__name__)

MENSAJE_SIN_MODELO = "Falta configurar el modelo de embeddings. Avise al administrador del sistema."
MENSAJE_NO_DISPONIBLE = (
    "No se pudo buscar evidencia porque el servicio de embeddings no respondió. "
    "Puede reintentar el análisis: continuará donde quedó."
)


def _cualitativos(licitacion_id: uuid.UUID) -> Any:
    return (
        select(Item)
        .join(Requisito, Item.requisito_id == Requisito.id)
        .where(
            Requisito.licitacion_id == licitacion_id,
            Item.tipo_verificacion == TipoVerificacion.CUALITATIVO,
        )
    )


async def _reordenar(consulta: str, candidatos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Orden del reranker; si falla, se conserva el de la búsqueda (no bloquea el análisis)."""
    try:
        orden = await nvidia.reordenar(consulta, [c["texto"] for c in candidatos])
    except Exception as exc:
        logger.warning(
            "Reranker no disponible (%s): se usa el orden de la búsqueda", type(exc).__name__
        )
        return candidatos
    reordenados = [
        {**candidatos[indice], "puntaje": round(puntaje, 4)} for indice, puntaje in orden
    ]
    vistos = {indice for indice, _ in orden}
    # Lo que el reranker no devolvió va al final, en el orden de la búsqueda.
    return reordenados + [c for i, c in enumerate(candidatos) if i not in vistos]


async def _buscar(
    organizacion_id: uuid.UUID, modelo: str, descripcion: str, vector: list[float]
) -> list[dict[str, Any]]:
    settings = get_settings()
    async with get_sessionmaker()() as session:
        cercanos = await fragmentos_cercanos(
            session,
            organizacion_id=organizacion_id,
            embedding=vector,
            origen=OrigenFragmento.EMPRESA,
            limite=settings.evidencia_candidatos,
            modelo=modelo,
        )
        relevantes = [
            (fragmento, distancia)
            for fragmento, distancia in cercanos
            if distancia <= settings.evidencia_distancia_maxima
        ]
        ids_fuentes = {f.fuente_conocimiento_id for f, _ in relevantes if f.fuente_conocimiento_id}
        fuentes = {
            fuente.id: fuente
            for fuente in await session.scalars(
                select(FuenteConocimiento).where(
                    FuenteConocimiento.id.in_(ids_fuentes),
                    FuenteConocimiento.organizacion_id == organizacion_id,
                )
            )
        }
    candidatos: list[dict[str, Any]] = []
    for fragmento, distancia in relevantes:
        fuente = fuentes.get(fragmento.fuente_conocimiento_id)  # type: ignore[arg-type]
        candidatos.append(
            {
                "fragmento_id": str(fragmento.id),
                "fuente_id": str(fuente.id) if fuente else None,
                "fuente": fuente.titulo if fuente else None,
                "tipo_fuente": fuente.tipo.value if fuente else None,
                "texto": fragmento.texto,
                "distancia": round(distancia, 4),
                "puntaje": None,
            }
        )
    if settings.rerank_activo and len(candidatos) > 1:
        candidatos = await _reordenar(descripcion, candidatos)
    return candidatos[: settings.evidencia_max]


async def _guardar(candidatos: dict[uuid.UUID, list[dict[str, Any]]]) -> None:
    async with get_sessionmaker()() as session:
        for item in await session.scalars(select(Item).where(Item.id.in_(candidatos))):
            item.candidatos = candidatos[item.id]
        await session.commit()


async def ejecutar(estado: EstadoAnalisis) -> None:
    """Busca evidencia para los ítems cualitativos que aún no la tienen. Idempotente."""
    settings = get_settings()
    licitacion_id = uuid.UUID(estado["licitacion_id"])
    organizacion_id = uuid.UUID(estado["organizacion_id"])
    modelo = settings.nvidia_model_embeddings

    async with get_sessionmaker()() as session:
        filas = await session.execute(
            _cualitativos(licitacion_id)
            .with_only_columns(Item.id, Item.descripcion)
            .where(Item.candidatos.is_(None))
            .order_by(Item.creado_en, Item.orden)
        )
        pendientes = [(identificador, descripcion) for identificador, descripcion in filas]
        indexados = 0
        if pendientes and modelo:
            indexados = (
                await session.scalar(
                    select(func.count())
                    .select_from(Fragmento)
                    .where(
                        Fragmento.organizacion_id == organizacion_id,
                        Fragmento.origen == OrigenFragmento.EMPRESA,
                        Fragmento.modelo_embedding == modelo,
                        Fragmento.embedding.is_not(None),
                    )
                )
                or 0
            )

    if pendientes and not modelo:
        raise AnalisisError(MENSAJE_SIN_MODELO)
    if pendientes and not indexados:
        # Sin base de conocimiento indexada no hay nada que buscar: no se gastan embeddings.
        logger.warning("Licitación %s: la organización no tiene base indexada", licitacion_id)
        await _guardar({identificador: [] for identificador, _ in pendientes})
        pendientes = []

    for inicio in range(0, len(pendientes), settings.embeddings_lote):
        lote = pendientes[inicio : inicio + settings.embeddings_lote]
        try:
            vectores = await nvidia.embeddings([descripcion for _, descripcion in lote], "query")
        except nvidia.NvidiaNoDisponibleError as exc:
            raise AnalisisError(MENSAJE_NO_DISPONIBLE) from exc
        if len(vectores) != len(lote):
            raise RuntimeError("NVIDIA devolvió un número de vectores distinto al de consultas")
        # Se guarda lote a lote: un reintento no vuelve a pagar los ítems ya resueltos.
        await _guardar(
            {
                identificador: await _buscar(organizacion_id, modelo or "", descripcion, vector)
                for (identificador, descripcion), vector in zip(lote, vectores, strict=True)
            }
        )

    async with get_sessionmaker()() as session:
        todos = list(
            await session.scalars(_cualitativos(licitacion_id).with_only_columns(Item.candidatos))
        )
    await progreso.anotar_detalle(
        uuid.UUID(estado["trabajo_id"]),
        evidencia={
            "items": len(todos),
            "con_evidencia": sum(1 for c in todos if c),
            "sin_evidencia": sum(1 for c in todos if not c),
            "base_indexada": bool(indexados) or any(todos),
            "rerank": settings.rerank_activo,
        },
    )
