"""Indexación de la base de conocimiento en pgvector (F-02, RF-03, ADR-013, HU-B06).

Cada fuente se convierte en fragmentos con significado propio (un contrato, una experiencia,
una certificación) y cada fragmento se vectoriza con el modelo de embeddings de NVIDIA.

- Los textos se envían por lotes y un texto ya vectorizado no se vuelve a enviar: su vector
  se guarda en `embeddings_cache` por organización y modelo (P-16).
- El índice se escribe al final, en una sola transacción que reemplaza los fragmentos
  anteriores: o queda completo, o queda como estaba. Nunca a medias ni con duplicados.
"""

import hashlib
import logging
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from analizador.core.config import get_settings
from analizador.db.conocimiento import filtro_version, version_activa
from analizador.db.session import get_sessionmaker
from analizador.integrations import nvidia
from analizador.integrations.anthropic_client import ModeloNoConfiguradoError
from analizador.models import (
    CambioConocimiento,
    DocumentoEmpresa,
    EmbeddingCache,
    Fragmento,
    FuenteConocimiento,
    Organizacion,
    TrabajoProcesamiento,
)
from analizador.models.enums import OrigenFragmento, TipoFuenteConocimiento
from analizador.pipeline import progreso
from analizador.pipeline.errores import AnalisisError

logger = logging.getLogger(__name__)

ETAPA = "indexacion"


@dataclass(frozen=True)
class FragmentoPendiente:
    fuente_id: uuid.UUID
    texto: str

    @property
    def hash(self) -> str:
        return hashlib.sha256(self.texto.encode("utf-8")).hexdigest()


# --- De registro a fragmentos ---------------------------------------------------------------


def _pesos(valor: Any) -> str:
    return f"${int(valor):,}".replace(",", ".") + " COP"


def _porcentaje(razon: Any) -> str:
    return f"{float(razon) * 100:.1f} %".replace(".", ",")


def _unir(*partes: str | None) -> str:
    return " ".join(parte.strip() for parte in partes if parte and parte.strip())


def _contrato(d: dict[str, Any]) -> list[str]:
    smmlv = f" ({d['valor_smmlv']} SMMLV)" if d.get("valor_smmlv") else ""
    codigos = d.get("codigos_unspsc") or []
    participacion = float(d.get("porcentaje_participacion", 100))
    return [
        _unir(
            f"Contrato {d['id']} ejecutado para {d['entidad_contratante']}.",
            f"Objeto: {d['objeto']}",
            f"Plazo de ejecución: del {d['fecha_inicio']} al {d['fecha_fin']}.",
            f"Valor: {_pesos(d['valor_cop'])}{smmlv}.",
            f"Códigos UNSPSC: {', '.join(codigos)}." if codigos else None,
            f"Participación del oferente: {participacion:g} %." if participacion < 100 else None,
            d.get("descripcion"),
        )
    ]


def _persona(d: dict[str, Any]) -> list[str]:
    titulos = "; ".join(
        f"{t['titulo']} ({t['nivel']}, {t['institucion']}, {t['anio']})"
        for t in d.get("titulos") or []
    )
    certificaciones = ", ".join(d.get("certificaciones") or [])
    perfil = _unir(
        f"{d['nombre']}, {d['profesion']}.",
        f"Experiencia profesional general: {float(d['anios_experiencia_general']):g} años.",
        f"Formación: {titulos}." if titulos else None,
        f"Certificaciones: {certificaciones}." if certificaciones else None,
    )
    experiencias = [
        _unir(
            f"{d['nombre']} ({d['profesion']}) se desempeñó como {e['cargo']} en {e['entidad']}",
            f"desde {e['fecha_inicio']} hasta {e.get('fecha_fin') or 'la fecha'}.",
            e.get("descripcion"),
        )
        for e in d.get("experiencias") or []
    ]
    return [perfil, *experiencias]


def _certificacion(d: dict[str, Any]) -> list[str]:
    vigencia = (
        f"vigente hasta {d['fecha_vencimiento']}"
        if d.get("fecha_vencimiento")
        else "sin vencimiento"
    )
    return [
        _unir(
            f"Certificación {d['nombre']} emitida por {d['entidad_emisora']}",
            f"el {d['fecha_expedicion']}, {vigencia}.",
            f"Alcance: {d['alcance']}" if d.get("alcance") else None,
        )
    ]


def _financiero(d: dict[str, Any]) -> list[str]:
    cobertura = d.get("razon_cobertura_intereses")
    return [
        _unir(
            f"Indicadores financieros del año {d['anio']}.",
            f"Índice de liquidez: {d['indice_liquidez']}.",
            f"Índice de endeudamiento: {_porcentaje(d['indice_endeudamiento'])}.",
            "Razón de cobertura de intereses: "
            + (
                f"{cobertura}."
                if cobertura is not None
                else "indeterminada (sin gastos de intereses)."
            ),
            f"Capital de trabajo: {_pesos(d['capital_trabajo_cop'])}.",
            f"Patrimonio: {_pesos(d['patrimonio_cop'])}.",
            f"Rentabilidad del patrimonio: {_porcentaje(d['rentabilidad_patrimonio'])}.",
            f"Rentabilidad del activo: {_porcentaje(d['rentabilidad_activo'])}.",
        )
    ]


def _capacidad(d: dict[str, Any]) -> list[str]:
    return [f"Capacidad de la empresa: {d['nombre']}. {d['descripcion']}"]


def _empresa(d: dict[str, Any]) -> list[str]:
    return [
        _unir(
            f"{d['razon_social']}, NIT {d['nit']}.",
            f"Domicilio: {d['ciudad']}." if d.get("ciudad") else None,
            f"Representante legal: {d['representante_legal']}."
            if d.get("representante_legal")
            else None,
        )
    ]


_CONVERSORES = {
    TipoFuenteConocimiento.CONTRATO: _contrato,
    TipoFuenteConocimiento.HOJA_DE_VIDA: _persona,
    TipoFuenteConocimiento.CERTIFICACION: _certificacion,
    TipoFuenteConocimiento.FINANCIERO: _financiero,
    TipoFuenteConocimiento.CAPACIDAD: _capacidad,
    TipoFuenteConocimiento.EMPRESA: _empresa,
}


def textos_de(tipo: TipoFuenteConocimiento, datos: dict[str, Any]) -> list[str]:
    """Fragmentos de texto de una fuente: cada uno se entiende sin leer los demás."""
    return _CONVERSORES[tipo](datos)


# --- Vectorización con caché ----------------------------------------------------------------


async def _vectores_en_cache(
    organizacion_id: uuid.UUID, modelo: str, hashes: set[str]
) -> dict[str, list[float]]:
    if not hashes:
        return {}
    async with get_sessionmaker()() as session:
        filas = await session.execute(
            select(EmbeddingCache.hash_texto, EmbeddingCache.embedding).where(
                EmbeddingCache.organizacion_id == organizacion_id,
                EmbeddingCache.modelo == modelo,
                EmbeddingCache.hash_texto.in_(hashes),
            )
        )
        return {hash_texto: [float(v) for v in vector] for hash_texto, vector in filas}


async def _guardar_en_cache(
    organizacion_id: uuid.UUID, modelo: str, vectores: dict[str, list[float]]
) -> None:
    async with get_sessionmaker()() as session:
        await session.execute(
            insert(EmbeddingCache)
            .values(
                [
                    {
                        "id": uuid.uuid4(),
                        "organizacion_id": organizacion_id,
                        "modelo": modelo,
                        "hash_texto": hash_texto,
                        "embedding": vector,
                    }
                    for hash_texto, vector in vectores.items()
                ]
            )
            .on_conflict_do_nothing()
        )
        await session.commit()


async def _vectorizar(
    trabajo_id: uuid.UUID,
    organizacion_id: uuid.UUID,
    modelo: str,
    pendientes: list[FragmentoPendiente],
) -> tuple[dict[str, list[float]], int]:
    """Vector de cada texto distinto, y cuántos hubo que pedir a NVIDIA."""
    por_hash = {p.hash: p.texto for p in pendientes}
    vectores = await _vectores_en_cache(organizacion_id, modelo, set(por_hash))
    faltantes = [h for h in por_hash if h not in vectores]
    lote = get_settings().embeddings_lote
    for inicio in range(0, len(faltantes), lote):
        hashes = faltantes[inicio : inicio + lote]
        nuevos = await nvidia.embeddings([por_hash[h] for h in hashes], "passage")
        if len(nuevos) != len(hashes):
            raise RuntimeError("NVIDIA devolvió un número de vectores distinto al de textos")
        recibidos = dict(zip(hashes, nuevos, strict=True))
        # Se guardan ya: si un lote posterior falla, el reintento no vuelve a pagarlos.
        await _guardar_en_cache(organizacion_id, modelo, recibidos)
        vectores.update(recibidos)
        await progreso.registrar_etapa(
            trabajo_id, ETAPA, min(95, 100 * (inicio + len(hashes)) // len(faltantes))
        )
    if len({len(v) for v in vectores.values()}) > 1:
        raise RuntimeError("Los vectores no tienen todos la misma dimensión")
    return vectores, len(faltantes)


# --- Trabajo de indexación ------------------------------------------------------------------


async def indexar(trabajo: TrabajoProcesamiento) -> None:
    """Indexa toda la base de conocimiento de la organización del trabajo."""
    organizacion_id = trabajo.organizacion_id
    modelo = get_settings().nvidia_model_embeddings
    if not modelo:
        raise AnalisisError(
            "Falta configurar el modelo de embeddings. Avise al administrador del sistema."
        )

    async with get_sessionmaker()() as session:
        fuentes = list(
            await session.scalars(
                select(FuenteConocimiento).where(
                    FuenteConocimiento.organizacion_id == organizacion_id,
                    filtro_version(await version_activa(session, organizacion_id)),
                )
            )
        )
    async with get_sessionmaker()() as session:
        cambio = await session.scalar(
            select(CambioConocimiento).where(CambioConocimiento.trabajo_id == trabajo.id)
        )
    if cambio is not None and cambio.estado in {"activado", "historico"}:
        return
    if cambio is not None:
        from analizador.contratos.conocimiento import BaseConocimiento
        from analizador.db.conocimiento import crear_fuentes

        fuentes = crear_fuentes(BaseConocimiento.model_validate(cambio.datos), organizacion_id)
        async with get_sessionmaker()() as session:
            previas = list(
                await session.scalars(
                    select(FuenteConocimiento).where(
                        FuenteConocimiento.organizacion_id == organizacion_id,
                        filtro_version(await version_activa(session, organizacion_id)),
                    )
                )
            )
        for fuente in fuentes:
            fuente.id = uuid.uuid4()
            for previa in previas:
                limpia = {k: v for k, v in (previa.datos or {}).items() if not k.startswith("_")}
                if previa.tipo == fuente.tipo and limpia == fuente.datos:
                    fuente.datos = {
                        **(fuente.datos or {}),
                        **{k: v for k, v in (previa.datos or {}).items() if k.startswith("_")},
                    }
                    fuente.storage_key = previa.storage_key
            for procedencia in cambio.procedencia:
                if procedencia.get("tipo") == fuente.tipo.value and all(
                    (fuente.datos or {}).get(k) == v
                    for k, v in procedencia.get("datos", {}).items()
                ):
                    documento = None
                    async with get_sessionmaker()() as session:
                        documento = (
                            await session.get(DocumentoEmpresa, cambio.documento_id)
                            if cambio.documento_id
                            else None
                        )
                    if documento:
                        fuente.storage_key = documento.storage_key
                        fuente.datos = {
                            **(fuente.datos or {}),
                            "_documento_id": str(documento.id),
                            "_pagina": procedencia["pagina"],
                            "_cita": procedencia["cita"],
                        }
    pendientes = [
        FragmentoPendiente(fuente.id, texto)
        for fuente in fuentes
        for texto in textos_de(fuente.tipo, fuente.datos or {})
    ]
    await progreso.registrar_etapa(trabajo.id, ETAPA, 0)

    try:
        vectores, enviados = await _vectorizar(trabajo.id, organizacion_id, modelo, pendientes)
    except (nvidia.NvidiaNoDisponibleError, ModeloNoConfiguradoError) as exc:
        logger.error("Indexación %s sin completar: %s", trabajo.id, exc)
        raise AnalisisError(
            "No se pudo indexar la base de conocimiento porque el servicio de embeddings no "
            "respondió. El índice anterior se conserva. Vuelva a lanzar la indexación."
        ) from exc

    async with get_sessionmaker()() as session:
        await session.scalar(
            select(Organizacion).where(Organizacion.id == organizacion_id).with_for_update()
        )
        vigentes = set(
            await session.scalars(
                select(FuenteConocimiento.id)
                .where(
                    FuenteConocimiento.organizacion_id == organizacion_id,
                    filtro_version(await version_activa(session, organizacion_id)),
                )
                .with_for_update()
            )
        )
        esperadas = (
            set(cambio.fuentes_anteriores) if cambio is not None else {str(f.id) for f in fuentes}
        )
        ultimo = await session.scalar(
            select(CambioConocimiento.id)
            .where(CambioConocimiento.organizacion_id == organizacion_id)
            .order_by(CambioConocimiento.secuencia.desc())
            .limit(1)
        )
        if {str(i) for i in vigentes} != esperadas or (cambio is not None and ultimo != cambio.id):
            if cambio is not None:
                obsoleto = await session.get(CambioConocimiento, cambio.id)
                if obsoleto:
                    obsoleto.estado = "obsoleto"
                if cambio.documento_id:
                    documento = await session.get(DocumentoEmpresa, cambio.documento_id)
                    if documento:
                        documento.estado = (
                            "aprobado"
                            if any(p.get("operacion") == "retiro" for p in cambio.procedencia)
                            else "revision"
                        )
                await session.commit()
            raise AnalisisError(
                "La base de conocimiento cambió durante la indexación. "
                "Se indexará la versión nueva."
            )
        if cambio is not None:
            for fuente in fuentes:
                fuente.datos = {**(fuente.datos or {}), "_revision": str(cambio.id)}
            anteriores = await session.scalars(
                select(CambioConocimiento).where(
                    CambioConocimiento.organizacion_id == organizacion_id,
                    CambioConocimiento.estado == "activado",
                )
            )
            for anterior in anteriores:
                anterior.estado = "historico"
            session.add_all(fuentes)
            await session.flush()
            guardado = await session.get(CambioConocimiento, cambio.id)
            if guardado is None:
                raise RuntimeError("Registro de procesamiento no encontrado")
            guardado.estado = "activado"
            if cambio.documento_id:
                documento = await session.get(DocumentoEmpresa, cambio.documento_id)
                if documento:
                    documento.estado = (
                        "retirado"
                        if any(p.get("operacion") == "retiro" for p in cambio.procedencia)
                        else "aprobado"
                    )
        await session.execute(
            delete(Fragmento).where(
                Fragmento.organizacion_id == organizacion_id,
                Fragmento.origen == OrigenFragmento.EMPRESA,
                Fragmento.fuente_conocimiento_id.in_([f.id for f in fuentes]),
            )
        )
        session.add_all(
            Fragmento(
                organizacion_id=organizacion_id,
                origen=OrigenFragmento.EMPRESA,
                fuente_conocimiento_id=p.fuente_id,
                texto=p.texto,
                hash_texto=p.hash,
                modelo_embedding=modelo,
                embedding=vectores[p.hash],
                pagina=next((f.datos or {}).get("_pagina") for f in fuentes if f.id == p.fuente_id),
                metadatos=next(
                    {k: v for k, v in (f.datos or {}).items() if k.startswith("_")}
                    for f in fuentes
                    if f.id == p.fuente_id
                ),
            )
            for p in pendientes
        )
        await session.commit()

    await progreso.actualizar_trabajo(
        trabajo.id,
        detalle={
            "fuentes": len(fuentes),
            "fragmentos": len(pendientes),
            "vectorizados": enviados,
            "reutilizados": len(vectores) - enviados,
            "modelo": modelo,
        },
    )
    logger.info(
        "Indexación %s: %d fuentes, %d fragmentos, %d vectorizados",
        trabajo.id,
        len(fuentes),
        len(pendientes),
        enviados,
    )
