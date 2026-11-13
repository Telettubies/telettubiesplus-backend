"""Etapa 6, parte del pliego: fragmentos vectorizados para el chat (HU-B24, ADR-013).

El pliego se divide por secciones en fragmentos de tamaño acotado, cada uno con el numeral y
la página de su sección, y se vectoriza con el modelo de embeddings de NVIDIA. Estos vectores
solo alimentan el chat: los requisitos no se extraen por búsqueda vectorial (ADR-012).

- Reutiliza la caché de embeddings por organización (P-16): un texto ya vectorizado no se
  vuelve a pagar, tampoco al reintentar.
- Escribe el índice en una sola transacción que reemplaza el anterior: completo o nada.
- Es de mejor esfuerzo: si NVIDIA no está disponible o falta el modelo, el análisis sigue y el
  chat de esa licitación queda no disponible. El chat es Should; el análisis, Must.
"""

import hashlib
import logging
import uuid

from sqlalchemy import delete, func, select

from analizador.core.config import get_settings
from analizador.db.session import get_sessionmaker
from analizador.integrations import nvidia
from analizador.integrations.anthropic_client import ModeloNoConfiguradoError
from analizador.models import Fragmento, Licitacion, Seccion
from analizador.models.enums import OrigenFragmento
from analizador.pipeline import progreso
from analizador.pipeline.estado import EstadoAnalisis
from analizador.pipeline.indexacion import _guardar_en_cache, _vectores_en_cache

logger = logging.getLogger(__name__)


def trozos(texto: str, maximo: int) -> list[str]:
    """Divide un texto en trozos de hasta `maximo` caracteres, respetando los párrafos."""
    resultado: list[str] = []
    actual = ""
    for parrafo in (p.strip() for p in texto.split("\n")):
        if not parrafo:
            continue
        while len(parrafo) > maximo:
            corte = parrafo.rfind(" ", 0, maximo)
            corte = corte if corte > maximo // 2 else maximo
            if actual:
                resultado.append(actual)
                actual = ""
            resultado.append(parrafo[:corte].strip())
            parrafo = parrafo[corte:].strip()
        if actual and len(actual) + 1 + len(parrafo) > maximo:
            resultado.append(actual)
            actual = ""
        actual = f"{actual}\n{parrafo}" if actual else parrafo
    if actual:
        resultado.append(actual)
    return resultado


def _hash(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


async def _vectorizar(
    organizacion_id: uuid.UUID, modelo: str, textos: list[str]
) -> dict[str, list[float]]:
    por_hash = {_hash(texto): texto for texto in textos}
    vectores = await _vectores_en_cache(organizacion_id, modelo, set(por_hash))
    faltantes = [h for h in por_hash if h not in vectores]
    lote = get_settings().embeddings_lote
    for inicio in range(0, len(faltantes), lote):
        hashes = faltantes[inicio : inicio + lote]
        nuevos = await nvidia.embeddings([por_hash[h] for h in hashes], "passage")
        if len(nuevos) != len(hashes):
            raise RuntimeError("NVIDIA devolvió un número de vectores distinto al de textos")
        recibidos = dict(zip(hashes, nuevos, strict=True))
        await _guardar_en_cache(organizacion_id, modelo, recibidos)
        vectores.update(recibidos)
    return vectores


async def ejecutar(estado: EstadoAnalisis) -> None:
    """Indexa el pliego para el chat si aún no lo está con el modelo vigente. Idempotente."""
    settings = get_settings()
    modelo = settings.nvidia_model_embeddings
    trabajo_id = uuid.UUID(estado["trabajo_id"])
    licitacion_id = uuid.UUID(estado["licitacion_id"])
    organizacion_id = uuid.UUID(estado["organizacion_id"])

    async with get_sessionmaker()() as session:
        secciones = (
            await session.execute(
                select(Seccion.numeral, Seccion.titulo, Seccion.pagina_inicio, Seccion.texto)
                .join(Licitacion, Seccion.licitacion_id == Licitacion.id)
                .where(
                    Licitacion.id == licitacion_id, Licitacion.organizacion_id == organizacion_id
                )
                .order_by(Seccion.orden)
            )
        ).all()
        existentes = await session.scalar(
            select(func.count())
            .select_from(Fragmento)
            .where(
                Fragmento.organizacion_id == organizacion_id,
                Fragmento.origen == OrigenFragmento.PLIEGO,
                Fragmento.licitacion_id == licitacion_id,
                Fragmento.modelo_embedding == modelo,
            )
        )

    pendientes = [
        (numeral, titulo, pagina, trozo)
        for numeral, titulo, pagina, texto in secciones
        for trozo in trozos(texto or "", settings.pliego_fragmento_caracteres)
    ]
    if not pendientes:
        return
    if modelo and existentes == len(pendientes):
        return
    try:
        if not modelo:
            raise ModeloNoConfiguradoError("NVIDIA_MODEL_EMBEDDINGS no está configurada")
        vectores = await _vectorizar(
            organizacion_id, modelo, list(dict.fromkeys(t for *_, t in pendientes))
        )
    except (nvidia.NvidiaNoDisponibleError, ModeloNoConfiguradoError) as exc:
        logger.warning(
            "Licitación %s: el pliego no se indexó para el chat (%s)",
            licitacion_id,
            type(exc).__name__,
        )
        await progreso.anotar_detalle(trabajo_id, chat={"indexado": False})
        return

    async with get_sessionmaker()() as session:
        await session.execute(
            delete(Fragmento).where(
                Fragmento.organizacion_id == organizacion_id,
                Fragmento.origen == OrigenFragmento.PLIEGO,
                Fragmento.licitacion_id == licitacion_id,
            )
        )
        session.add_all(
            Fragmento(
                organizacion_id=organizacion_id,
                origen=OrigenFragmento.PLIEGO,
                licitacion_id=licitacion_id,
                texto=texto,
                hash_texto=_hash(texto),
                pagina=pagina,
                embedding=vectores[_hash(texto)],
                modelo_embedding=modelo,
                metadatos={"numeral": numeral, "titulo": titulo},
            )
            for numeral, titulo, pagina, texto in pendientes
        )
        await session.commit()
    await progreso.anotar_detalle(
        trabajo_id, chat={"indexado": True, "fragmentos": len(pendientes)}
    )
