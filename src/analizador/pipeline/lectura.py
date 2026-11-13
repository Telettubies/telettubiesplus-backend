"""Etapa 1: lectura del pliego página por página (RF-16, ADR-010, P-09, HU-B10).

- Páginas digitales: texto con PyMuPDF en orden de lectura y tablas con pdfplumber, que se
  insertan en su sitio como filas ordenadas (`celda | celda`).
- Páginas sin texto extraíble: se consideran escaneadas y pasan por Tesseract, que además
  entrega la caja de cada palabra para poder resaltarla después (HU-B15).
- Si Tesseract no logra texto legible, Claude con visión transcribe la página (sin cajas).
- Si tampoco, la página queda marcada como no leída y el análisis lo informa.

El PDF es contenido no confiable (S-09): se abre solo en el worker, con máximo de páginas, y
todo el trabajo de CPU corre en un hilo para no bloquear el bucle de eventos.
"""

import asyncio
import io
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import pdfplumber
import pymupdf
import pytesseract
from PIL import Image
from sqlalchemy import delete

from analizador.core.config import Settings, get_settings
from analizador.db.session import get_sessionmaker
from analizador.integrations import almacenamiento, anthropic_client
from analizador.models import Licitacion, PaginaPliego
from analizador.models.enums import OrigenPagina
from analizador.pipeline import progreso
from analizador.pipeline.errores import AnalisisError
from analizador.pipeline.estado import EstadoAnalisis

logger = logging.getLogger(__name__)

# Tipo de bloque de PyMuPDF que contiene texto (1 es imagen).
BLOQUE_DE_TEXTO = 0

Transcriptor = Callable[[bytes], Awaitable[str | None]]


@dataclass
class Pagina:
    numero: int  # desde 1
    texto: str
    origen: OrigenPagina
    # Cajas de las palabras leídas por OCR, en coordenadas relativas a la página (0..1):
    # [{"t": "palabra", "x0": .., "y0": .., "x1": .., "y1": ..}, ...]. Nulo si no hay OCR.
    palabras: list[dict[str, Any]] | None = None
    # Imagen de la página, solo mientras espera la transcripción con visión.
    imagen_png: bytes | None = field(default=None, repr=False)


# --- Páginas digitales ----------------------------------------------------------------------


def _tabla_como_texto(filas: list[list[str | None]]) -> str:
    """Cada fila en una línea, celdas separadas por ` | ` y sin saltos internos."""
    lineas = []
    for fila in filas:
        celdas = [" ".join((celda or "").split()) for celda in fila]
        if any(celdas):
            lineas.append(" | ".join(celdas))
    return "\n".join(lineas)


def _tablas(pagina_plumber: Any) -> list[tuple[tuple[float, float, float, float], str]]:
    tablas = []
    for tabla in pagina_plumber.find_tables():
        texto = _tabla_como_texto(tabla.extract())
        if texto:
            tablas.append((tuple(tabla.bbox), texto))
    return tablas


def _dentro(bloque: tuple[Any, ...], caja: tuple[float, float, float, float]) -> bool:
    centro_x, centro_y = (bloque[0] + bloque[2]) / 2, (bloque[1] + bloque[3]) / 2
    return bool(caja[0] <= centro_x <= caja[2] and caja[1] <= centro_y <= caja[3])


def _texto_digital(pagina: Any, pagina_plumber: Any | None) -> str:
    """Texto de la página en orden de lectura, con cada tabla en su lugar como texto ordenado."""
    bloques = [b for b in pagina.get_text("blocks", sort=True) if b[6] == BLOQUE_DE_TEXTO]
    # pdfplumber detecta tablas por sus líneas: sin dibujos vectoriales no hay nada que buscar.
    tablas = _tablas(pagina_plumber) if pagina_plumber is not None and pagina.get_drawings() else []
    piezas: list[tuple[float, float, str]] = [(caja[1], caja[0], texto) for caja, texto in tablas]
    for bloque in bloques:
        if not any(_dentro(bloque, caja) for caja, _ in tablas):
            piezas.append((bloque[1], bloque[0], bloque[4].strip()))
    piezas.sort(key=lambda pieza: (round(pieza[0], 1), pieza[1]))
    return "\n".join(texto for _, _, texto in piezas if texto)


# --- OCR ------------------------------------------------------------------------------------


def _ocr(imagen_png: bytes, settings: Settings) -> tuple[str, list[dict[str, Any]], float] | None:
    """Texto, cajas relativas de las palabras y confianza media; None si Tesseract no está."""
    imagen = Image.open(io.BytesIO(imagen_png))
    try:
        datos = pytesseract.image_to_data(
            imagen, lang=settings.ocr_idioma, output_type=pytesseract.Output.DICT
        )
    except (pytesseract.TesseractNotFoundError, pytesseract.TesseractError) as exc:
        logger.warning("Tesseract no disponible para OCR: %s", type(exc).__name__)
        return None

    ancho, alto = imagen.size
    lineas: dict[tuple[int, int, int], list[str]] = {}
    palabras: list[dict[str, Any]] = []
    confianzas: list[float] = []
    for i, texto in enumerate(datos["text"]):
        texto = texto.strip()
        confianza = float(datos["conf"][i])
        if not texto or confianza < 0:
            continue
        clave = (datos["block_num"][i], datos["par_num"][i], datos["line_num"][i])
        lineas.setdefault(clave, []).append(texto)
        confianzas.append(confianza)
        x, y, w, h = datos["left"][i], datos["top"][i], datos["width"][i], datos["height"][i]
        palabras.append(
            {
                "t": texto,
                "x0": round(x / ancho, 5),
                "y0": round(y / alto, 5),
                "x1": round((x + w) / ancho, 5),
                "y1": round((y + h) / alto, 5),
            }
        )
    texto_pagina = "\n".join(" ".join(linea) for linea in lineas.values())
    media = sum(confianzas) / len(confianzas) if confianzas else 0.0
    return texto_pagina, palabras, media


def _es_legible(texto: str, confianza: float, settings: Settings) -> bool:
    return (
        len(texto.strip()) >= settings.ocr_min_caracteres
        and confianza >= settings.ocr_confianza_minima
    )


def _leer_escaneada(numero: int, pagina: Any, settings: Settings) -> Pagina:
    imagen_png = pagina.get_pixmap(dpi=settings.ocr_dpi).tobytes("png")
    resultado = _ocr(imagen_png, settings)
    if resultado is not None:
        texto, palabras, confianza = resultado
        if _es_legible(texto, confianza, settings):
            return Pagina(numero, texto, OrigenPagina.OCR, palabras)
    # Sin texto legible: queda pendiente de la transcripción con visión.
    return Pagina(numero, "", OrigenPagina.NO_LEIDA, imagen_png=imagen_png)


# --- Documento ------------------------------------------------------------------------------


def _abrir(contenido: bytes, settings: Settings) -> Any:
    try:
        documento = pymupdf.open(stream=contenido, filetype="pdf")  # type: ignore[no-untyped-call]
    except Exception as exc:
        raise AnalisisError(
            "No se pudo abrir el PDF: el archivo está dañado o no es un PDF válido."
        ) from exc
    if documento.needs_pass:
        raise AnalisisError(
            "El PDF está protegido con contraseña. Suba una versión sin protección."
        )
    if documento.page_count == 0:
        raise AnalisisError("El PDF no tiene páginas.")
    if documento.page_count > settings.max_pdf_paginas:
        raise AnalisisError(
            f"El pliego tiene {documento.page_count} páginas y el máximo permitido es "
            f"{settings.max_pdf_paginas}."
        )
    return documento


def leer_paginas(contenido: bytes, settings: Settings | None = None) -> list[Pagina]:
    """Lee todas las páginas (síncrono, uso intensivo de CPU). No incluye el paso de visión."""
    settings = settings or get_settings()
    documento = _abrir(contenido, settings)
    paginas: list[Pagina] = []
    try:
        with pdfplumber.open(io.BytesIO(contenido)) as plumber:
            for indice in range(documento.page_count):
                pagina = documento[indice]
                numero = indice + 1
                try:
                    pagina_plumber = plumber.pages[indice]
                except Exception:  # pdfplumber no pudo con esta página: se sigue sin tablas
                    pagina_plumber = None
                try:
                    texto = _texto_digital(pagina, pagina_plumber)
                except Exception:
                    logger.warning("No se pudieron extraer las tablas de la página %d", numero)
                    texto = _texto_digital(pagina, None)
                if len(texto.strip()) >= settings.ocr_min_caracteres:
                    paginas.append(Pagina(numero, texto, OrigenPagina.DIGITAL))
                elif pagina.get_images():
                    # Sin texto extraíble pero con imágenes: página escaneada.
                    paginas.append(_leer_escaneada(numero, pagina, settings))
                else:
                    # Página en blanco o casi: no hay nada que reconocer.
                    paginas.append(Pagina(numero, texto, OrigenPagina.DIGITAL))
    finally:
        documento.close()
    return paginas


async def leer_pdf(
    contenido: bytes, transcribir: Transcriptor | None = None, *, limite_paginas: int | None = None
) -> list[Pagina]:
    """Lee el pliego completo. `transcribir` es el respaldo con visión para páginas ilegibles."""
    settings = get_settings()
    if limite_paginas is not None:
        settings = settings.model_copy(
            update={"max_pdf_paginas": min(settings.max_pdf_paginas, limite_paginas)}
        )
    paginas = await asyncio.to_thread(leer_paginas, contenido, settings)

    con_vision = 0
    for pagina in paginas:
        imagen, pagina.imagen_png = pagina.imagen_png, None
        if imagen is None:
            continue
        if transcribir is None or con_vision >= settings.ocr_vision_max_paginas:
            continue  # se queda como no leída
        con_vision += 1
        try:
            texto = await transcribir(imagen)
        except Exception:
            logger.exception("Falló la transcripción con visión de la página %d", pagina.numero)
            texto = None
        if texto and len(texto.strip()) >= settings.ocr_min_caracteres:
            pagina.texto, pagina.origen = texto.strip(), OrigenPagina.VISION
    return paginas


# --- Etapa del pipeline ---------------------------------------------------------------------


def _transcriptor() -> Transcriptor | None:
    """Respaldo con visión, solo si hay modelo y clave configurados."""
    settings = get_settings()
    if not settings.anthropic_model_ocr or settings.anthropic_api_key is None:
        return None
    return anthropic_client.transcribir_pagina


async def ejecutar(estado: EstadoAnalisis) -> None:
    """Lee el PDF de la licitación y guarda sus páginas. Repetirla no duplica páginas."""
    licitacion_id = uuid.UUID(estado["licitacion_id"])
    async with get_sessionmaker()() as session:
        licitacion = await session.get(Licitacion, licitacion_id)
    if licitacion is None:
        raise RuntimeError(f"No existe la licitación {licitacion_id}")
    try:
        contenido = await almacenamiento.descargar(licitacion.storage_key)
    except almacenamiento.AlmacenamientoError as exc:
        raise AnalisisError(
            "No se pudo obtener el archivo del pliego. Reintente en unos minutos."
        ) from exc

    paginas = await leer_pdf(contenido, _transcriptor())

    async with get_sessionmaker()() as session:
        await session.execute(
            delete(PaginaPliego).where(PaginaPliego.licitacion_id == licitacion_id)
        )
        session.add_all(
            PaginaPliego(
                licitacion_id=licitacion_id,
                numero=pagina.numero,
                texto=pagina.texto,
                origen=pagina.origen,
                palabras=pagina.palabras,
            )
            for pagina in paginas
        )
        await session.commit()

    def numeros(origen: OrigenPagina) -> list[int]:
        return [p.numero for p in paginas if p.origen == origen]

    no_leidas = numeros(OrigenPagina.NO_LEIDA)
    if no_leidas:
        logger.warning("Licitación %s: %d páginas sin leer", licitacion_id, len(no_leidas))
    await progreso.anotar_detalle(
        uuid.UUID(estado["trabajo_id"]),
        paginas=len(paginas),
        paginas_ocr=len(numeros(OrigenPagina.OCR)),
        paginas_vision=len(numeros(OrigenPagina.VISION)),
        paginas_no_leidas=no_leidas,
    )
