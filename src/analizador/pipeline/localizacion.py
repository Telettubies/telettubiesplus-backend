"""Etapa 4b: localizar cada requisito en el PDF (RF-26, P-21, R-17, HU-B15).

La ubicación la calcula el código, nunca el modelo: se busca el texto literal del requisito
entre las palabras de las páginas de su sección y se guardan las zonas a resaltar en
coordenadas relativas a la página (0..1), independientes del zoom del visor.

- Exacta: el texto literal completo aparece, palabra por palabra.
- Aproximada: solo aparece su comienzo; se resalta hasta donde coincide.
- Solo página: no se encontró; se guarda la primera página de la sección, sin zonas.

Las palabras salen del propio PDF en las páginas digitales (PyMuPDF) y de las cajas del OCR en
las escaneadas (HU-B10). El mismo algoritmo sirve para ambas. Las páginas transcritas con
visión no tienen cajas: sus requisitos quedan en "solo página".
"""

import asyncio
import logging
import unicodedata
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import pymupdf
from sqlalchemy import delete, select

from analizador.db.session import get_sessionmaker
from analizador.integrations import almacenamiento
from analizador.models import Licitacion, PaginaPliego, Requisito, Seccion, UbicacionPliego
from analizador.models.enums import OrigenPagina, PrecisionUbicacion
from analizador.pipeline import progreso
from analizador.pipeline.errores import AnalisisError
from analizador.pipeline.estado import EstadoAnalisis

logger = logging.getLogger(__name__)

# Palabras iniciales que deben coincidir para dar una ubicación aproximada.
MINIMO_APROXIMADA = 4
DECIMALES = 5


@dataclass(frozen=True)
class Palabra:
    """Una palabra de una página con su caja en coordenadas relativas (0..1)."""

    pagina: int
    x0: float
    y0: float
    x1: float
    y1: float
    texto: str

    @property
    def clave(self) -> str:
        return normalizar(self.texto)


@dataclass(frozen=True)
class Ubicacion:
    pagina: int
    zonas: list[dict[str, float | int]]
    precision: PrecisionUbicacion


def normalizar(texto: str) -> str:
    """Minúsculas, sin tildes y solo letras y dígitos: `1,5 %` y `1.5%` comparan igual."""
    sin_tildes = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in sin_tildes if c.isalnum()).casefold()


def claves(texto: str) -> list[str]:
    return [clave for clave in (normalizar(parte) for parte in texto.split()) if clave]


def _zonas(palabras: Sequence[Palabra]) -> list[dict[str, float | int]]:
    """Une las palabras encontradas en un rectángulo por línea."""
    zonas: list[dict[str, float | int]] = []
    actual: list[Palabra] = []

    def cerrar() -> None:
        if actual:
            zonas.append(
                {
                    "pagina": actual[0].pagina,
                    "x0": round(min(p.x0 for p in actual), DECIMALES),
                    "y0": round(min(p.y0 for p in actual), DECIMALES),
                    "x1": round(max(p.x1 for p in actual), DECIMALES),
                    "y1": round(max(p.y1 for p in actual), DECIMALES),
                }
            )
            actual.clear()

    for palabra in palabras:
        if actual:
            anterior = actual[-1]
            alto = max(anterior.y1 - anterior.y0, 1e-6)
            misma_linea = (
                palabra.pagina == anterior.pagina
                and abs(palabra.y0 - anterior.y0) < alto * 0.6
                and palabra.x0 >= anterior.x0
            )
            if not misma_linea:
                cerrar()
        actual.append(palabra)
    cerrar()
    return zonas


def _coincidencia_mas_larga(buscadas: list[str], disponibles: list[str]) -> tuple[int, int]:
    """Posición y longitud del tramo de `disponibles` que más palabras iniciales de
    `buscadas` reproduce de corrido. Devuelve (-1, 0) si no coincide ni la primera."""
    mejor_posicion, mejor_longitud = -1, 0
    primera = buscadas[0]
    for posicion, clave in enumerate(disponibles):
        if clave != primera:
            continue
        longitud = 1
        while (
            longitud < len(buscadas)
            and posicion + longitud < len(disponibles)
            and disponibles[posicion + longitud] == buscadas[longitud]
        ):
            longitud += 1
        if longitud > mejor_longitud:
            mejor_posicion, mejor_longitud = posicion, longitud
            if longitud == len(buscadas):
                break
    return mejor_posicion, mejor_longitud


def localizar(texto_literal: str, palabras: Sequence[Palabra], pagina_seccion: int) -> Ubicacion:
    """Ubicación del texto entre las palabras de las páginas de su sección, en orden."""
    buscadas = claves(texto_literal)
    utiles = [p for p in palabras if p.clave]
    if buscadas and utiles:
        posicion, longitud = _coincidencia_mas_larga(buscadas, [p.clave for p in utiles])
        encontradas = utiles[posicion : posicion + longitud] if posicion >= 0 else []
        if longitud == len(buscadas):
            return Ubicacion(encontradas[0].pagina, _zonas(encontradas), PrecisionUbicacion.EXACTA)
        if longitud >= min(MINIMO_APROXIMADA, len(buscadas)):
            return Ubicacion(
                encontradas[0].pagina, _zonas(encontradas), PrecisionUbicacion.APROXIMADA
            )
    return Ubicacion(pagina_seccion, [], PrecisionUbicacion.SOLO_PAGINA)


# --- Palabras de cada página ----------------------------------------------------------------


def palabras_digitales(contenido: bytes, numeros: set[int]) -> dict[int, list[Palabra]]:
    """Palabras con su caja relativa de las páginas indicadas del PDF (síncrono)."""
    resultado: dict[int, list[Palabra]] = {}
    if not numeros:
        return resultado
    documento: Any = pymupdf.open(stream=contenido, filetype="pdf")  # type: ignore[no-untyped-call]
    try:
        for numero in sorted(numeros):
            if not 1 <= numero <= documento.page_count:
                continue
            pagina = documento[numero - 1]
            ancho, alto = pagina.rect.width, pagina.rect.height
            resultado[numero] = [
                Palabra(numero, x0 / ancho, y0 / alto, x1 / ancho, y1 / alto, texto)
                for x0, y0, x1, y1, texto, *_ in pagina.get_text("words", sort=True)
            ]
    finally:
        documento.close()
    return resultado


def palabras_de_ocr(numero: int, cajas: Sequence[dict[str, Any]]) -> list[Palabra]:
    return [
        Palabra(numero, float(c["x0"]), float(c["y0"]), float(c["x1"]), float(c["y1"]), str(c["t"]))
        for c in cajas
    ]


# --- Etapa del pipeline ---------------------------------------------------------------------


async def ejecutar(estado: EstadoAnalisis) -> None:
    """Localiza los requisitos que aún no tienen ubicación. Repetirla no duplica ninguna."""
    licitacion_id = uuid.UUID(estado["licitacion_id"])
    async with get_sessionmaker()() as session:
        filas = await session.execute(
            select(Requisito.id, Requisito.texto_literal, Seccion.pagina_inicio, Seccion.pagina_fin)
            .join(Seccion, Requisito.seccion_id == Seccion.id)
            .outerjoin(UbicacionPliego, UbicacionPliego.requisito_id == Requisito.id)
            .where(Requisito.licitacion_id == licitacion_id, UbicacionPliego.id.is_(None))
        )
        pendientes = [(i, texto, inicio, fin) for i, texto, inicio, fin in filas]
        if pendientes:
            licitacion = await session.get(Licitacion, licitacion_id)
            paginas = list(
                await session.scalars(
                    select(PaginaPliego).where(PaginaPliego.licitacion_id == licitacion_id)
                )
            )

    if pendientes:
        necesarias = {n for _, _, inicio, fin in pendientes for n in range(inicio, fin + 1)}
        por_pagina: dict[int, list[Palabra]] = {
            p.numero: palabras_de_ocr(p.numero, p.palabras)
            for p in paginas
            if p.numero in necesarias and p.origen == OrigenPagina.OCR and p.palabras
        }
        digitales = {
            p.numero for p in paginas if p.numero in necesarias and p.origen == OrigenPagina.DIGITAL
        }
        if digitales:
            if licitacion is None:
                raise RuntimeError(f"No existe la licitación {licitacion_id}")
            try:
                contenido = await almacenamiento.descargar(licitacion.storage_key)
            except almacenamiento.AlmacenamientoError as exc:
                raise AnalisisError(
                    "No se pudo obtener el archivo del pliego. Reintente en unos minutos."
                ) from exc
            por_pagina.update(await asyncio.to_thread(palabras_digitales, contenido, digitales))

        ubicaciones = {
            requisito_id: localizar(
                texto,
                [p for numero in range(inicio, fin + 1) for p in por_pagina.get(numero, [])],
                inicio,
            )
            for requisito_id, texto, inicio, fin in pendientes
        }
        async with get_sessionmaker()() as session:
            await session.execute(
                delete(UbicacionPliego).where(UbicacionPliego.requisito_id.in_(ubicaciones))
            )
            session.add_all(
                UbicacionPliego(
                    requisito_id=requisito_id,
                    pagina=ubicacion.pagina,
                    zonas=ubicacion.zonas,
                    precision=ubicacion.precision,
                )
                for requisito_id, ubicacion in ubicaciones.items()
            )
            await session.commit()

    # Medida de calidad: proporción de ubicaciones exactas del pliego (R-17).
    async with get_sessionmaker()() as session:
        precisiones = list(
            await session.scalars(
                select(UbicacionPliego.precision)
                .join(Requisito, UbicacionPliego.requisito_id == Requisito.id)
                .where(Requisito.licitacion_id == licitacion_id)
            )
        )
    conteo = {precision.value: precisiones.count(precision) for precision in PrecisionUbicacion}
    await progreso.anotar_detalle(uuid.UUID(estado["trabajo_id"]), ubicaciones=conteo)
    logger.info("Licitación %s: ubicaciones %s", licitacion_id, conteo)
