"""Fábricas para las pruebas de las etapas 6 a 10: un análisis con requisitos, ítems y evidencia."""

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from analizador.models import (
    Fragmento,
    FuenteConocimiento,
    Item,
    Licitacion,
    Requisito,
    Seccion,
    TrabajoProcesamiento,
)
from analizador.models.enums import (
    OrigenFragmento,
    TipoFuenteConocimiento,
    TipoRequisito,
    TipoTrabajo,
    TipoVerificacion,
)
from analizador.pipeline.estado import EstadoAnalisis
from tests.apoyo import Identidad, crear_organizacion

MODELO_EMBEDDINGS = "modelo-de-embeddings-de-prueba"


@dataclass(frozen=True)
class Analisis:
    quien: Identidad
    licitacion_id: uuid.UUID
    trabajo_id: uuid.UUID
    seccion_id: uuid.UUID
    estado: EstadoAnalisis


async def crear_analisis(
    sesion: AsyncSession,
    quien: Identidad | None = None,
    texto_seccion: str = "Texto de la sección del pliego.",
) -> Analisis:
    """Licitación con su trabajo de análisis y una sección ya procesada."""
    quien = quien or await crear_organizacion(sesion)
    licitacion = Licitacion(
        organizacion_id=quien.organizacion_id,
        creado_por=quien.usuario_id,
        titulo="Pliego",
        nombre_archivo="pliego.pdf",
        storage_key=f"organizaciones/{quien.organizacion_id}/pliegos/{uuid.uuid4()}.pdf",
    )
    sesion.add(licitacion)
    await sesion.flush()
    trabajo = TrabajoProcesamiento(
        organizacion_id=quien.organizacion_id,
        tipo=TipoTrabajo.ANALISIS,
        licitacion_id=licitacion.id,
    )
    seccion = Seccion(
        licitacion_id=licitacion.id,
        orden=1,
        numeral="2",
        titulo="REQUISITOS",
        pagina_inicio=1,
        pagina_fin=1,
        texto=texto_seccion,
        tiene_requisitos=True,
        procesada=True,
    )
    sesion.add_all([trabajo, seccion])
    await sesion.commit()
    return Analisis(
        quien=quien,
        licitacion_id=licitacion.id,
        trabajo_id=trabajo.id,
        seccion_id=seccion.id,
        estado=EstadoAnalisis(
            trabajo_id=str(trabajo.id),
            licitacion_id=str(licitacion.id),
            organizacion_id=str(quien.organizacion_id),
        ),
    )


async def agregar_requisito(
    sesion: AsyncSession,
    analisis: Analisis,
    *,
    numeral: str,
    texto: str,
    items: list[str | tuple[str, dict[str, Any]]],
    tipo: TipoRequisito = TipoRequisito.TECNICO,
    ambiguo: bool = False,
    motivo_ambiguedad: str | None = None,
) -> Requisito:
    """Requisito con sus ítems. Un ítem `(descripción, parámetros)` es numérico."""
    requisito = Requisito(
        licitacion_id=analisis.licitacion_id,
        seccion_id=analisis.seccion_id,
        numeral=numeral,
        tipo=tipo,
        texto_literal=texto,
        ambiguo=ambiguo,
        motivo_ambiguedad=motivo_ambiguedad,
    )
    for orden, item in enumerate(items, start=1):
        if isinstance(item, str):
            requisito.items.append(
                Item(orden=orden, descripcion=item, tipo_verificacion=TipoVerificacion.CUALITATIVO)
            )
        else:
            descripcion, parametros = item
            requisito.items.append(
                Item(
                    orden=orden,
                    descripcion=descripcion,
                    tipo_verificacion=TipoVerificacion.NUMERICO,
                    parametros=parametros,
                )
            )
    sesion.add(requisito)
    await sesion.commit()
    return requisito


async def agregar_fragmento(
    sesion: AsyncSession,
    quien: Identidad,
    texto: str,
    embedding: list[float],
    *,
    titulo: str = "Contrato de prueba",
    tipo: TipoFuenteConocimiento = TipoFuenteConocimiento.CONTRATO,
    modelo: str = MODELO_EMBEDDINGS,
    datos: dict[str, Any] | None = None,
) -> Fragmento:
    """Fuente de conocimiento con un fragmento ya indexado."""
    fuente = FuenteConocimiento(
        organizacion_id=quien.organizacion_id, tipo=tipo, titulo=titulo, datos=datos or {}
    )
    sesion.add(fuente)
    await sesion.flush()
    fragmento = Fragmento(
        organizacion_id=quien.organizacion_id,
        origen=OrigenFragmento.EMPRESA,
        fuente_conocimiento_id=fuente.id,
        texto=texto,
        embedding=embedding,
        modelo_embedding=modelo,
    )
    sesion.add(fragmento)
    await sesion.commit()
    return fragmento
