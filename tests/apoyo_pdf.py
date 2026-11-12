"""Fábrica de PDF de prueba: páginas digitales, tablas con líneas y páginas escaneadas."""

from dataclasses import dataclass, field
from typing import Any

import pymupdf

MARGEN = 72
INTERLINEA = 16


@dataclass
class Tabla:
    filas: list[list[str]]
    ancho_columna: float = 150
    alto_fila: float = 20


@dataclass
class PaginaDePrueba:
    """Contenido de una página: líneas de texto y tablas, en el orden en que se dibujan."""

    contenido: list[str | Tabla] = field(default_factory=list)
    # Si es True, la página se convierte en una imagen: no tiene texto extraíble.
    escaneada: bool = False
    tamano_letra: float = 11


def _dibujar(pagina: Any, descripcion: PaginaDePrueba) -> None:
    y = MARGEN + 10.0
    for elemento in descripcion.contenido:
        if isinstance(elemento, str):
            pagina.insert_text((MARGEN, y), elemento, fontsize=descripcion.tamano_letra)
            y += INTERLINEA * descripcion.tamano_letra / 11
            continue
        y += 6
        for i, fila in enumerate(elemento.filas):
            for j, celda in enumerate(fila):
                caja = pymupdf.Rect(
                    MARGEN + j * elemento.ancho_columna,
                    y + i * elemento.alto_fila,
                    MARGEN + (j + 1) * elemento.ancho_columna,
                    y + (i + 1) * elemento.alto_fila,
                )
                pagina.draw_rect(caja)
                pagina.insert_text((caja.x0 + 4, caja.y1 - 6), celda, fontsize=10)
        y += len(elemento.filas) * elemento.alto_fila + INTERLINEA


def crear_pdf(paginas: list[PaginaDePrueba | list[str]]) -> bytes:
    """PDF con una página por elemento. Una lista de cadenas equivale a una página de texto."""
    documento = pymupdf.open()  # type: ignore[no-untyped-call]
    for descripcion in paginas:
        if isinstance(descripcion, list):
            descripcion = PaginaDePrueba(list(descripcion))
        pagina = documento.new_page()
        if not descripcion.escaneada:
            _dibujar(pagina, descripcion)
            continue
        # Se dibuja en un documento aparte, se convierte en imagen y se pega: como un escáner.
        temporal = pymupdf.open()  # type: ignore[no-untyped-call]
        origen = temporal.new_page()
        _dibujar(origen, descripcion)
        imagen = origen.get_pixmap(dpi=200)
        pagina.insert_image(pagina.rect, pixmap=imagen)
        temporal.close()
    contenido: bytes = documento.tobytes()
    documento.close()
    return contenido


def pdf_protegido() -> bytes:
    documento = pymupdf.open()  # type: ignore[no-untyped-call]
    documento.new_page().insert_text((MARGEN, MARGEN), "Contenido reservado")
    contenido: bytes = documento.tobytes(
        encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="secreta", owner_pw="secreta"
    )
    documento.close()
    return contenido
