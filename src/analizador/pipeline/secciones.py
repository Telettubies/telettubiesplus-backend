"""Etapa 2: dividir el pliego en secciones ligadas a su numeral y páginas (RF-02, ADR-012, HU-B11).

Se hace con reglas, sin modelos. El texto del pliego es la unión de sus páginas y cada sección
es un tramo contiguo de ese texto: la concatenación de todas las secciones reproduce el pliego
exacto, de modo que ningún fragmento queda fuera de la extracción (N-04).

Un encabezado abre una sección. Se reconocen:
- numerales jerárquicos al inicio de línea: `3.2.1 Capacidad jurídica`, `4. CRONOGRAMA`;
- palabras de división: `CAPÍTULO IV`, `TÍTULO II`, `SECCIÓN 3`, `ANEXO 2`, `FORMATO 5`.

No son encabezados las entradas de la tabla de contenido (terminan en puntos y un número de
página) ni los ítems de una lista numerada (`1. Carta de presentación firmada.`).
"""

import logging
import re
import uuid
from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import delete, select

from analizador.core.config import get_settings
from analizador.db.session import get_sessionmaker
from analizador.models import PaginaPliego, Seccion
from analizador.pipeline import progreso
from analizador.pipeline.errores import AnalisisError
from analizador.pipeline.estado import EstadoAnalisis

logger = logging.getLogger(__name__)

TITULO_MAXIMO = 160
PREAMBULO = "Preámbulo"

# `3.2.1 Título`, `3.2.1. Título`, `4. TÍTULO`, `4 - TÍTULO`
_NUMERAL = re.compile(
    r"^[ \t]*(?P<numeral>\d{1,2}(?:\.\d{1,2}){0,5})\.?"
    r"(?:[ \t]*[-–—:)][ \t]*|[ \t]+)(?P<titulo>\S.*)$"
)
_DIVISION = re.compile(
    r"^[ \t]*(?P<palabra>CAP[IÍ]TULO|T[IÍ]TULO|SECCI[OÓ]N|ANEXO|FORMATO|FORMULARIO|AP[EÉ]NDICE)"
    r"[ \t]+(?:No\.?[ \t]*|N[°º][ \t]*)?(?P<orden>[IVXLCDM]+|\d{1,3}|[A-Z])\b"
    r"[ \t]*[.:\-–—]?[ \t]*(?P<titulo>.*)$",
    re.IGNORECASE,
)
# Entrada de tabla de contenido: `3.2 Requisitos habilitantes ........ 15`
_ENTRADA_DE_INDICE = re.compile(r"(?:\.[ \t]?){3,}[ \t]*\d{1,4}[ \t]*$|[ \t]{3,}\d{1,4}[ \t]*$")
_LETRAS = re.compile(r"[^\W\d_]")


@dataclass(frozen=True)
class SeccionDetectada:
    orden: int
    numeral: str | None
    titulo: str | None
    pagina_inicio: int
    pagina_fin: int
    texto: str


@dataclass(frozen=True)
class _Encabezado:
    posicion: int  # desplazamiento en el texto del pliego donde empieza la línea
    numeral: str
    titulo: str


def _proporcion_mayusculas(texto: str) -> float:
    letras = _LETRAS.findall(texto)
    return sum(letra.isupper() for letra in letras) / len(letras) if letras else 0.0


def _titulo_valido(titulo: str) -> bool:
    return len(titulo) <= TITULO_MAXIMO and len(_LETRAS.findall(titulo)) >= 3


def _encabezado_numerado(linea: str) -> tuple[str, str] | None:
    coincidencia = _NUMERAL.match(linea)
    if coincidencia is None:
        return None
    numeral, titulo = coincidencia["numeral"], coincidencia["titulo"].strip()
    if not _titulo_valido(titulo) or not titulo[0].isalpha() or not titulo[0].isupper():
        return None
    en_mayusculas = _proporcion_mayusculas(titulo) >= 0.6
    if "." not in numeral and not en_mayusculas:
        # `1. Carta de presentación.` es un ítem de lista; `1. INFORMACIÓN GENERAL`, un capítulo.
        return None
    primera = titulo.split()[0]
    if not en_mayusculas and primera.isupper() and len(primera) > 1:
        # `1.5 SMMLV por cada día`: una cifra seguida de una sigla, no un título.
        return None
    if "." in numeral and titulo.endswith((".", ";", ",")) and len(titulo) > 80:
        return None  # una frase larga numerada, no un título
    return numeral, titulo


def _encabezado_de_division(linea: str, siguiente: str) -> tuple[str, str] | None:
    coincidencia = _DIVISION.match(linea)
    if coincidencia is None:
        return None
    palabra, orden, titulo = coincidencia["palabra"], coincidencia["orden"], coincidencia["titulo"]
    # En mitad de una frase (`el anexo 2 contiene…`) la palabra va en minúscula.
    if not palabra[0].isupper() or (len(orden) == 1 and orden.isalpha() and not orden.isupper()):
        return None
    titulo = titulo.strip()
    if titulo and (not _titulo_valido(titulo) or _proporcion_mayusculas(titulo) < 0.6):
        if palabra.isupper():
            titulo = ""  # `ANEXO 2 del presente pliego…`: se conserva el numeral, sin título
        else:
            return None
    if not titulo:
        candidato = siguiente.strip()
        if candidato and _titulo_valido(candidato) and _proporcion_mayusculas(candidato) >= 0.6:
            titulo = candidato
    return f"{palabra.upper()} {orden.upper()}", titulo


def _encabezados(texto: str) -> list[_Encabezado]:
    lineas = texto.split("\n")
    encabezados: list[_Encabezado] = []
    posicion = 0
    for indice, linea in enumerate(lineas):
        if linea.strip() and not _ENTRADA_DE_INDICE.search(linea):
            siguiente = lineas[indice + 1] if indice + 1 < len(lineas) else ""
            detectado = _encabezado_numerado(linea) or _encabezado_de_division(linea, siguiente)
            if detectado is not None:
                encabezados.append(_Encabezado(posicion, *detectado))
        posicion += len(linea) + 1
    return encabezados


def _cortes_por_tamano(texto: str, inicio: int, fin: int, maximo: int) -> list[int]:
    """Puntos de corte dentro de [inicio, fin) para que ningún tramo supere `maximo`.

    Corta en un salto de párrafo si lo hay en el último tercio del tramo; si no, en un salto
    de línea; si tampoco, en el máximo exacto.
    """
    cortes: list[int] = []
    actual = inicio
    while fin - actual > maximo:
        limite = actual + maximo
        minimo = actual + maximo * 2 // 3
        corte = texto.rfind("\n\n", minimo, limite)
        corte = corte + 2 if corte != -1 else texto.rfind("\n", minimo, limite) + 1
        if corte <= actual:
            corte = limite
        cortes.append(corte)
        actual = corte
    return cortes


def dividir(
    paginas: Sequence[tuple[int, str]], maximo: int | None = None
) -> list[SeccionDetectada]:
    """Secciones del pliego a partir de sus páginas `(número, texto)`, en orden.

    `"".join(s.texto for s in secciones)` es igual a `"\\n".join(texto de cada página)`.
    """
    if not paginas:
        return []
    maximo = maximo or get_settings().seccion_max_caracteres
    texto = "\n".join(contenido for _, contenido in paginas)
    numeros = [numero for numero, _ in paginas]
    inicios: list[int] = []
    posicion = 0
    for _, contenido in paginas:
        inicios.append(posicion)
        posicion += len(contenido) + 1

    def pagina_de(desplazamiento: int) -> int:
        return numeros[max(0, bisect_right(inicios, desplazamiento) - 1)]

    encabezados = _encabezados(texto)
    # Texto previo al primer encabezado: sección propia, salvo que sea solo espacio en blanco.
    tramos: list[tuple[int, str | None, str | None]] = []
    primero = encabezados[0].posicion if encabezados else len(texto)
    if texto[:primero].strip():
        tramos.append((0, None, PREAMBULO))
    elif encabezados:
        encabezados[0] = _Encabezado(0, encabezados[0].numeral, encabezados[0].titulo)
    tramos.extend((e.posicion, e.numeral, e.titulo or None) for e in encabezados)
    if not tramos:
        tramos.append((0, None, PREAMBULO))

    secciones: list[SeccionDetectada] = []
    for indice, (inicio, numeral, titulo) in enumerate(tramos):
        fin = tramos[indice + 1][0] if indice + 1 < len(tramos) else len(texto)
        limites = [inicio, *_cortes_por_tamano(texto, inicio, fin, maximo), fin]
        partes = len(limites) - 1
        for parte in range(partes):
            desde, hasta = limites[parte], limites[parte + 1]
            contenido = texto[desde:hasta]
            # La página final es la del último carácter con contenido, no la del salto final.
            ultimo = desde + max(len(contenido.rstrip()) - 1, 0)
            nombre = titulo
            if partes > 1:
                nombre = f"{titulo or ''} (parte {parte + 1} de {partes})".strip()
            secciones.append(
                SeccionDetectada(
                    orden=len(secciones) + 1,
                    numeral=numeral,
                    titulo=nombre,
                    pagina_inicio=pagina_de(desde),
                    pagina_fin=pagina_de(ultimo),
                    texto=contenido,
                )
            )
    return secciones


# --- Etapa del pipeline ---------------------------------------------------------------------


async def ejecutar(estado: EstadoAnalisis) -> None:
    """Divide el pliego leído y guarda sus secciones pendientes de procesar.

    Repetirla reemplaza las secciones anteriores (y lo que colgara de ellas): no duplica.
    """
    licitacion_id = uuid.UUID(estado["licitacion_id"])
    async with get_sessionmaker()() as session:
        filas = await session.execute(
            select(PaginaPliego.numero, PaginaPliego.texto)
            .where(PaginaPliego.licitacion_id == licitacion_id)
            .order_by(PaginaPliego.numero)
        )
        paginas = [(numero, texto) for numero, texto in filas]
    secciones = dividir(paginas)
    if not any(seccion.texto.strip() for seccion in secciones):
        raise AnalisisError(
            "No se pudo leer texto en ninguna página del pliego. "
            "Si es un documento escaneado, verifique que la imagen sea legible."
        )

    # Completitud verificable por longitud: nada del pliego queda fuera de las secciones.
    longitud_pliego = sum(len(texto) for _, texto in paginas) + len(paginas) - 1
    if sum(len(seccion.texto) for seccion in secciones) != longitud_pliego:
        raise RuntimeError("Las secciones no cubren todo el texto del pliego")

    async with get_sessionmaker()() as session:
        await session.execute(delete(Seccion).where(Seccion.licitacion_id == licitacion_id))
        session.add_all(
            Seccion(
                licitacion_id=licitacion_id,
                orden=seccion.orden,
                numeral=seccion.numeral,
                titulo=seccion.titulo[:500] if seccion.titulo else None,
                pagina_inicio=seccion.pagina_inicio,
                pagina_fin=seccion.pagina_fin,
                texto=seccion.texto,
                procesada=False,
            )
            for seccion in secciones
        )
        await session.commit()

    await progreso.anotar_detalle(uuid.UUID(estado["trabajo_id"]), secciones=len(secciones))
    logger.info("Licitación %s dividida en %d secciones", licitacion_id, len(secciones))
