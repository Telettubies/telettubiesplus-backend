"""Matriz del dashboard a partir de datos persistidos (HU-B21, RF-12, RF-26)."""

import uuid
from collections import Counter
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from analizador.contratos.decision import EstadoMatriz, Recomendacion
from analizador.models import (
    Item,
    Licitacion,
    Miembro,
    PaginaPliego,
    Requisito,
    Resultado,
    Revision,
    Seccion,
)
from analizador.models.enums import PrecisionUbicacion, TipoRequisito
from analizador.pipeline import cobertura, decision
from analizador.schemas import (
    CoberturaMatrizOut,
    EvidenciaMatrizOut,
    ItemMatrizOut,
    ResultadoMatrizOut,
    RevisionOut,
    UbicacionMatrizOut,
    ZonaMatrizOut,
)


def _evidencia(datos: dict[str, Any]) -> EvidenciaMatrizOut:
    similitud = datos.get("similitud")
    return EvidenciaMatrizOut(
        fuente_id=datos.get("fuente_id"),
        fuente_nombre=datos.get("fuente"),
        # Las citas del modelo llevan un fragmento completo como soporte interno.
        cita_literal=(datos.get("cita") if datos.get("tipo") == "cita" else datos.get("texto"))
        or "",
        cita_verificada=datos.get("verificada") is True,
        similitud=similitud / 100 if isinstance(similitud, int | float) else None,
    )


def _ubicacion(requisito: Requisito, seccion: Seccion) -> UbicacionMatrizOut:
    ubicacion = requisito.ubicacion
    if ubicacion is None:
        return UbicacionMatrizOut(
            pagina=seccion.pagina_inicio, precision=PrecisionUbicacion.SOLO_PAGINA, zonas=[]
        )
    return UbicacionMatrizOut(
        pagina=ubicacion.pagina,
        precision=ubicacion.precision,
        zonas=[
            ZonaMatrizOut(
                pagina=int(zona.get("pagina", ubicacion.pagina)),
                x=zona["x0"],
                y=zona["y0"],
                width=zona["x1"] - zona["x0"],
                height=zona["y1"] - zona["y0"],
            )
            for zona in ubicacion.zonas
        ],
    )


def _revision(revision: Revision, autores: dict[uuid.UUID, str]) -> RevisionOut:
    salida = RevisionOut.model_validate(revision)
    salida.autor_email = autores.get(revision.usuario_id)
    return salida


def _item(
    item: Item, requisito: Requisito, seccion: Seccion, autores: dict[uuid.UUID, str]
) -> ItemMatrizOut:
    resultado = item.resultado
    estado = decision.estado_matriz(resultado)
    evidencias = [_evidencia(datos) for datos in resultado.evidencia] if resultado else []
    return ItemMatrizOut(
        id=item.id,
        requisito_id=requisito.id,
        resultado_id=resultado.id if resultado else None,
        codigo_req=str(requisito.id),
        numeral=requisito.numeral or seccion.numeral or "s/n",
        categoria=requisito.tipo,
        texto_requisito_literal=requisito.texto_literal,
        texto_requisito_verificado=requisito.texto_verificado,
        descripcion_item=item.descripcion,
        estado=estado,
        estado_propuesto=resultado.estado if resultado else None,
        motivo_revision=(resultado.motivo_revision if resultado else "Falta verificar el ítem.")
        if estado == EstadoMatriz.REVISION
        else None,
        es_ambiguo=requisito.ambiguo,
        evidencia=evidencias[0] if evidencias else None,
        evidencias=evidencias,
        faltante=resultado.faltante if resultado else None,
        confianza=resultado.confianza if resultado else None,
        ubicacion=_ubicacion(requisito, seccion),
        revisiones=[_revision(r, autores) for r in resultado.revisiones] if resultado else [],
    )


async def items_de(
    session: AsyncSession, licitacion: Licitacion, organizacion_id: uuid.UUID
) -> list[ItemMatrizOut]:
    """Ítems de la matriz en el orden del pliego, solo de la organización (S-03)."""
    filas = await session.execute(
        select(Requisito, Seccion)
        .select_from(Requisito)
        .join(Licitacion, Requisito.licitacion_id == Licitacion.id)
        .join(Seccion, Requisito.seccion_id == Seccion.id)
        .where(
            Licitacion.id == licitacion.id,
            Licitacion.organizacion_id == organizacion_id,
            Seccion.licitacion_id == licitacion.id,
        )
        .options(
            selectinload(Requisito.ubicacion),
            selectinload(Requisito.items)
            .selectinload(Item.resultado)
            .selectinload(Resultado.revisiones),
        )
        .order_by(Seccion.orden, Requisito.creado_en, Requisito.id)
    )
    autores = {
        usuario_id: email
        for usuario_id, email in await session.execute(
            select(Miembro.usuario_id, Miembro.email).where(
                Miembro.organizacion_id == organizacion_id
            )
        )
    }
    return [
        _item(item, requisito, seccion, autores)
        for requisito, seccion in filas
        for item in sorted(requisito.items, key=lambda item: (item.orden, item.id))
    ]


async def construir(
    session: AsyncSession,
    licitacion: Licitacion,
    organizacion_id: uuid.UUID,
    recomendacion: Recomendacion,
    justificacion: str,
    url_pdf: str,
) -> ResultadoMatrizOut:
    items = await items_de(session, licitacion, organizacion_id)
    informe = await cobertura.calcular(session, licitacion.id)
    paginas = await session.scalar(
        select(func.max(PaginaPliego.numero)).where(PaginaPliego.licitacion_id == licitacion.id)
    )
    resumen = Counter(item.estado for item in items)
    categorias = {categoria: Counter[EstadoMatriz]() for categoria in TipoRequisito}
    for item in items:
        categorias[item.categoria][item.estado] += 1
    return ResultadoMatrizOut(
        licitacion_id=licitacion.id,
        nombre=licitacion.titulo,
        fecha_carga=licitacion.creado_en,
        pdf_nombre=licitacion.nombre_archivo,
        pdf_paginas=paginas,
        pdf_url_temporal=url_pdf,
        recomendacion=recomendacion,
        justificacion=justificacion,
        cobertura=CoberturaMatrizOut(
            secciones_procesadas=informe.procesadas,
            secciones_total=informe.total,
            numerales_no_procesados=[
                seccion.numeral or f"pág. {seccion.pagina_inicio}"
                for seccion in informe.no_procesadas
            ],
        ),
        resumen_estados={estado: resumen[estado] for estado in EstadoMatriz},
        categorias={
            categoria: {estado: conteo[estado] for estado in EstadoMatriz}
            for categoria, conteo in categorias.items()
        },
        items=items,
        brechas=sorted(
            (item for item in items if decision.es_brecha(item.estado)),
            key=lambda item: item.categoria != TipoRequisito.HABILITANTE,
        ),
    )
