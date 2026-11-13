"""Documentos empresariales: carga privada, revisión y publicación diferida."""

import hashlib
import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, File, HTTPException, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from analizador.api.deps import AdminDep, ContextoDep, PublicadorDep, SessionDep
from analizador.api.routers.conocimiento import _encolar_indexacion
from analizador.api.routers.licitaciones import _leer_pdf
from analizador.contratos.conocimiento import BaseConocimiento
from analizador.core.config import get_settings
from analizador.db.conocimiento import filtro_version, version_activa
from analizador.integrations import almacenamiento
from analizador.models import (
    CambioConocimiento,
    DocumentoEmpresa,
    FuenteConocimiento,
    Organizacion,
    TrabajoProcesamiento,
)
from analizador.models.enums import EstadoTrabajo, TipoFuenteConocimiento, TipoTrabajo
from analizador.schemas import TrabajoOut

router = APIRouter(prefix="/api/conocimiento/documentos", tags=["documentos empresariales"])


class DocumentoResumen(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    nombre: str
    estado: str
    creado_en: datetime
    error: str | None = None


class DocumentoOut(DocumentoResumen):
    candidatos: list[dict[str, Any]]
    paginas: list[dict[str, Any]]


class ProcedenciaIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tipo: TipoFuenteConocimiento
    pagina: int = Field(ge=1)
    cita: str = Field(min_length=1, max_length=20000)
    datos: dict[str, Any] = Field(min_length=1)


class AprobacionIn(BaseModel):
    version_base: str = "legacy"
    base: BaseConocimiento
    procedencia: list[ProcedenciaIn] = Field(min_length=1, max_length=1000)


async def obtener(
    session: SessionDep,
    organizacion_id: uuid.UUID,
    identificador: uuid.UUID,
    bloquear: bool = False,
) -> DocumentoEmpresa:
    consulta = select(DocumentoEmpresa).where(
        DocumentoEmpresa.id == identificador, DocumentoEmpresa.organizacion_id == organizacion_id
    )
    documento = await session.scalar(consulta.with_for_update() if bloquear else consulta)
    if documento is None:
        raise HTTPException(404, "Documento no encontrado")
    return documento


@router.get("", response_model=list[DocumentoResumen])
async def listar(contexto: ContextoDep, session: SessionDep) -> Any:
    documentos = list(
        await session.scalars(
            select(DocumentoEmpresa)
            .where(DocumentoEmpresa.organizacion_id == contexto.organizacion_id)
            .order_by(DocumentoEmpresa.creado_en.desc())
        )
    )
    fallos = await session.execute(
        select(TrabajoProcesamiento, CambioConocimiento.documento_id)
        .outerjoin(CambioConocimiento, CambioConocimiento.trabajo_id == TrabajoProcesamiento.id)
        .where(
            TrabajoProcesamiento.organizacion_id == contexto.organizacion_id,
            TrabajoProcesamiento.estado == EstadoTrabajo.FALLIDO,
        )
        .order_by(TrabajoProcesamiento.creado_en.desc())
    )
    errores: dict[str, str | None] = {}
    for trabajo, documento_id in fallos:
        clave = (
            str(documento_id)
            if documento_id
            else str((trabajo.detalle or {}).get("documento_id", ""))
        )
        errores.setdefault(clave, trabajo.error)
    return [
        DocumentoResumen.model_validate(doc).model_copy(
            update={"error": errores.get(str(doc.id)) if "fallid" in doc.estado else None}
        )
        for doc in documentos
    ]


@router.post("", response_model=DocumentoOut, status_code=202)
async def cargar(
    contexto: AdminDep,
    session: SessionDep,
    publicador: PublicadorDep,
    archivo: Annotated[UploadFile, File()],
) -> Any:
    contenido = await _leer_pdf(archivo)
    hash_pdf = hashlib.sha256(contenido).hexdigest()
    # Serializar cargas de la misma organizacion evita duplicar documentos/trabajos.
    await session.scalar(
        select(Organizacion).where(Organizacion.id == contexto.organizacion_id).with_for_update()
    )
    existente = await session.scalar(
        select(DocumentoEmpresa).where(
            DocumentoEmpresa.organizacion_id == contexto.organizacion_id,
            DocumentoEmpresa.hash_pdf == hash_pdf,
        )
    )
    if existente:
        return existente
    cantidad = await session.scalar(
        select(func.count())
        .select_from(DocumentoEmpresa)
        .where(DocumentoEmpresa.organizacion_id == contexto.organizacion_id)
    )
    if (cantidad or 0) >= get_settings().max_documentos_empresa:
        raise HTTPException(429, "Se alcanzo el limite de documentos empresariales")
    clave = (
        f"{almacenamiento.prefijo_organizacion(contexto.organizacion_id)}empresa/{uuid.uuid4()}.pdf"
    )
    await almacenamiento.subir_pdf(clave, contenido)
    documento = DocumentoEmpresa(
        id=uuid.uuid4(),
        organizacion_id=contexto.organizacion_id,
        nombre=(archivo.filename or "documento.pdf")[:300],
        hash_pdf=hash_pdf,
        storage_key=clave,
        estado="en_cola",
        paginas=[],
        candidatos=[],
    )
    trabajo = TrabajoProcesamiento(
        id=uuid.uuid4(),
        organizacion_id=contexto.organizacion_id,
        tipo=TipoTrabajo.INDEXACION,
        detalle={"operacion": "documento", "documento_id": str(documento.id)},
    )
    session.add_all([documento, trabajo])
    try:
        await session.commit()
    except Exception:
        await session.rollback()
        await almacenamiento.eliminar(clave)
        raise
    try:
        await _encolar_indexacion(publicador, trabajo.id)
    except HTTPException:
        documento.estado = "fallido"
        await session.commit()
        raise
    return documento


@router.get("/{identificador}/pdf-url")
async def pdf_url(
    identificador: uuid.UUID, contexto: ContextoDep, session: SessionDep, response: Response
) -> dict[str, str]:
    response.headers["Cache-Control"] = "no-store"
    documento = await obtener(session, contexto.organizacion_id, identificador)
    if not almacenamiento.pertenece_a(documento.storage_key, contexto.organizacion_id):
        raise HTTPException(404, "Documento no encontrado")
    return {"url": await almacenamiento.url_temporal(documento.storage_key)}


@router.post("/{identificador}/reintentar", response_model=TrabajoOut, status_code=202)
async def reintentar(
    identificador: uuid.UUID, contexto: AdminDep, session: SessionDep, publicador: PublicadorDep
) -> Any:
    documento = await obtener(session, contexto.organizacion_id, identificador, True)
    if documento.estado != "fallido":
        raise HTTPException(409, "Solo se puede reintentar un documento fallido")
    documento.estado = "en_cola"
    trabajo = TrabajoProcesamiento(
        organizacion_id=contexto.organizacion_id,
        tipo=TipoTrabajo.INDEXACION,
        detalle={"operacion": "documento", "documento_id": str(documento.id)},
    )
    session.add(trabajo)
    await session.commit()
    try:
        await _encolar_indexacion(publicador, trabajo.id)
    except HTTPException:
        documento.estado = "indexacion_fallida" if documento.aprobado_por else "fallido"
        await session.commit()
        raise
    return trabajo


@router.post("/{identificador}/aprobar", response_model=TrabajoOut, status_code=202)
async def aprobar(
    identificador: uuid.UUID,
    datos: AprobacionIn,
    contexto: AdminDep,
    session: SessionDep,
    publicador: PublicadorDep,
) -> Any:
    await session.scalar(
        select(Organizacion).where(Organizacion.id == contexto.organizacion_id).with_for_update()
    )
    if datos.version_base != await version_activa(session, contexto.organizacion_id):
        raise HTTPException(
            409,
            "La base activa cambio durante su revision. "
            "Vuelva a abrir el documento y revise los cambios.",
        )
    documento = await obtener(session, contexto.organizacion_id, identificador, True)
    if documento.estado != "revision":
        raise HTTPException(409, "El documento no esta pendiente de revision")
    paginas = {p["numero"]: p["texto"] for p in documento.paginas}
    if not datos.procedencia:
        raise HTTPException(422, "Debe indicar al menos una evidencia del documento")
    for procedencia in datos.procedencia:
        evidencia = procedencia.model_dump(mode="json")
        pagina, cita = evidencia.get("pagina"), evidencia.get("cita", "")
        if (
            pagina not in paginas
            or not isinstance(cita, str)
            or not cita.strip()
            or " ".join(cita.split()) not in " ".join(paginas[pagina].split())
        ):
            raise HTTPException(422, "La evidencia no aparece en la pagina indicada")
    anteriores = list(
        await session.scalars(
            select(FuenteConocimiento.id).where(
                FuenteConocimiento.organizacion_id == contexto.organizacion_id,
                filtro_version(await version_activa(session, contexto.organizacion_id)),
            )
        )
    )
    trabajo = TrabajoProcesamiento(
        id=uuid.uuid4(), organizacion_id=contexto.organizacion_id, tipo=TipoTrabajo.INDEXACION
    )
    cambio = CambioConocimiento(
        organizacion_id=contexto.organizacion_id,
        trabajo_id=trabajo.id,
        datos=datos.base.model_dump(mode="json"),
        fuentes_anteriores=sorted(str(i) for i in anteriores),
        documento_id=documento.id,
        procedencia=[p.model_dump(mode="json") for p in datos.procedencia],
    )
    documento.estado = "indexando"
    documento.aprobado_por = contexto.usuario.usuario_id
    session.add_all([cambio, trabajo])
    await session.commit()
    try:
        await _encolar_indexacion(publicador, trabajo.id)
    except HTTPException:
        documento.estado = "indexacion_fallida" if documento.aprobado_por else "fallido"
        await session.commit()
        raise
    return trabajo


@router.get("/base/actual")
async def base_actual(contexto: ContextoDep, session: SessionDep) -> dict[str, Any]:
    version = await version_activa(session, contexto.organizacion_id)
    grupos = {
        "empresa": "empresa",
        "financiero": "financiero",
        "contrato": "contratos",
        "hoja_de_vida": "personas",
        "certificacion": "certificaciones",
        "capacidad": "capacidades",
    }
    base: dict[str, Any] = {
        "empresa": {"razon_social": "", "nit": ""},
        "financiero": [],
        "contratos": [],
        "personas": [],
        "certificaciones": [],
        "capacidades": [],
    }
    fuentes = await session.scalars(
        select(FuenteConocimiento).where(
            FuenteConocimiento.organizacion_id == contexto.organizacion_id,
            filtro_version(version),
        )
    )
    for fuente in fuentes:
        datos = {k: v for k, v in (fuente.datos or {}).items() if not k.startswith("_")}
        if fuente.tipo.value == "empresa":
            base["empresa"] = datos
        else:
            base[grupos[fuente.tipo.value]].append(datos)
    base["_version"] = version
    return base


@router.get("/fuentes/{fuente_id}/pdf-url")
async def pdf_fuente(
    fuente_id: uuid.UUID, contexto: ContextoDep, session: SessionDep, response: Response
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    fuente = await session.scalar(
        select(FuenteConocimiento).where(
            FuenteConocimiento.id == fuente_id,
            FuenteConocimiento.organizacion_id == contexto.organizacion_id,
        )
    )
    if (
        fuente is None
        or not fuente.storage_key
        or not almacenamiento.pertenece_a(fuente.storage_key, contexto.organizacion_id)
    ):
        raise HTTPException(404, "Esta fuente no tiene un PDF empresarial asociado")
    return {
        "url": await almacenamiento.url_temporal(fuente.storage_key),
        "pagina": (fuente.datos or {}).get("_pagina"),
    }


@router.post("/{identificador}/rechazar", response_model=DocumentoOut)
async def rechazar(identificador: uuid.UUID, contexto: AdminDep, session: SessionDep) -> Any:
    documento = await obtener(session, contexto.organizacion_id, identificador, True)
    if documento.estado != "revision":
        raise HTTPException(409, "Solo se pueden rechazar documentos pendientes de revision")
    documento.estado = "rechazado"
    await session.commit()
    return documento


@router.post("/{identificador}/retirar", response_model=TrabajoOut, status_code=202)
async def retirar(
    identificador: uuid.UUID, contexto: AdminDep, session: SessionDep, publicador: PublicadorDep
) -> Any:
    """Retira datos de la versión activa; conserva el PDF y la evidencia histórica."""
    await session.scalar(
        select(Organizacion).where(Organizacion.id == contexto.organizacion_id).with_for_update()
    )
    documento = await obtener(session, contexto.organizacion_id, identificador, True)
    if documento.estado != "aprobado":
        raise HTTPException(409, "Solo se pueden retirar documentos aprobados")
    fuentes = list(
        await session.scalars(
            select(FuenteConocimiento).where(
                FuenteConocimiento.organizacion_id == contexto.organizacion_id,
                filtro_version(await version_activa(session, contexto.organizacion_id)),
            )
        )
    )
    base = await base_actual(contexto, session)
    base.pop("_version", None)
    grupos = {
        "financiero": "financiero",
        "contrato": "contratos",
        "hoja_de_vida": "personas",
        "certificacion": "certificaciones",
        "capacidad": "capacidades",
    }
    for fuente in fuentes:
        if (fuente.datos or {}).get("_documento_id") != str(documento.id):
            continue
        if fuente.tipo.value == "empresa":
            raise HTTPException(
                409, "Reemplace primero los datos generales de la empresa con otra fuente"
            )
        limpio = {k: v for k, v in (fuente.datos or {}).items() if not k.startswith("_")}
        grupo = grupos[fuente.tipo.value]
        base[grupo] = [dato for dato in base[grupo] if dato != limpio]
    trabajo = TrabajoProcesamiento(
        id=uuid.uuid4(), organizacion_id=contexto.organizacion_id, tipo=TipoTrabajo.INDEXACION
    )
    cambio = CambioConocimiento(
        organizacion_id=contexto.organizacion_id,
        trabajo_id=trabajo.id,
        datos=BaseConocimiento.model_validate(base).model_dump(mode="json"),
        fuentes_anteriores=sorted(str(f.id) for f in fuentes),
        documento_id=documento.id,
        procedencia=[{"operacion": "retiro"}],
    )
    documento.estado = "retirando"
    session.add_all([trabajo, cambio])
    await session.commit()
    try:
        await _encolar_indexacion(publicador, trabajo.id)
    except HTTPException:
        documento.estado = "indexacion_fallida"
        await session.commit()
        raise
    return trabajo


class LimitesDocumentosOut(BaseModel):
    max_documentos: int
    max_paginas: int
    max_mb: int


@router.get("/limites", response_model=LimitesDocumentosOut)
async def limites_documentos(contexto: ContextoDep) -> LimitesDocumentosOut:
    settings = get_settings()
    return LimitesDocumentosOut(
        max_documentos=settings.max_documentos_empresa,
        max_paginas=settings.documento_empresa_max_paginas,
        max_mb=settings.max_pdf_mb,
    )


@router.get("/{identificador}", response_model=DocumentoOut)
async def detalle(identificador: uuid.UUID, contexto: ContextoDep, session: SessionDep) -> Any:
    return await obtener(session, contexto.organizacion_id, identificador)
