"""Exportación de la matriz a Excel (RF-22, HU-B29).

El archivo se arma con los mismos ítems que entrega `/resultado`, desde PostgreSQL y sin
llamar a ningún modelo. S-12: un valor que empieza con `=`, `+`, `-`, `@`, tabulación o
retorno de carro se antepone con un apóstrofo y se guarda como texto, para que Excel no lo
interprete como fórmula.
"""

from io import BytesIO

from openpyxl import Workbook
from openpyxl.cell.cell import Cell
from openpyxl.styles import Alignment, Font
from openpyxl.worksheet.worksheet import Worksheet

from analizador.contratos.decision import EstadoMatriz, Recomendacion
from analizador.schemas import ItemMatrizOut

PELIGROSOS = ("=", "+", "-", "@", "\t", "\r")

COLUMNAS: tuple[tuple[str, int], ...] = (
    ("Numeral", 12),
    ("Página", 8),
    ("Categoría", 14),
    ("Requisito", 60),
    ("Ítem verificable", 45),
    ("Estado", 20),
    ("Evidencia", 60),
    ("Evidencia verificada", 12),
    ("Faltante", 45),
    ("Revisado por una persona", 14),
    ("Revisor", 30),
)

ESTADOS = {
    EstadoMatriz.CUMPLE: "Cumple",
    EstadoMatriz.CUMPLE_PARCIALMENTE: "Cumple parcialmente",
    EstadoMatriz.NO_CUMPLE: "No cumple",
    EstadoMatriz.REVISION: "Requiere revisión",
}

RECOMENDACIONES = {
    Recomendacion.PRESENTARSE: "Presentarse",
    Recomendacion.PRESENTARSE_CON_RIESGO: "Presentarse con riesgo",
    Recomendacion.NO_PRESENTARSE: "No presentarse",
    Recomendacion.CONDICIONADA: "Condicionada a revisión",
}


def neutralizar(valor: str | int | None) -> str | int | None:
    """Evita la inyección de fórmulas (S-12): los textos peligrosos empiezan con apóstrofo."""
    if isinstance(valor, str) and valor.startswith(PELIGROSOS):
        return "'" + valor
    return valor


def _escribir(hoja: Worksheet, fila: int, valores: list[str | int | None]) -> None:
    for columna, valor in enumerate(valores, start=1):
        celda: Cell = hoja.cell(row=fila, column=columna, value=neutralizar(valor))
        if isinstance(celda.value, str):
            celda.data_type = "s"  # nunca fórmula, aunque el texto lo parezca
        celda.alignment = Alignment(wrap_text=True, vertical="top")


def _fila(item: ItemMatrizOut) -> list[str | int | None]:
    revisores = [r.autor_email or str(r.usuario_id) for r in item.revisiones]
    return [
        item.numeral,
        item.ubicacion.pagina,
        item.categoria.value,
        item.texto_requisito_literal,
        item.descripcion_item,
        ESTADOS[item.estado],
        item.evidencia.cita_literal if item.evidencia else None,
        ("Sí" if item.evidencia.cita_verificada else "No") if item.evidencia else None,
        item.faltante,
        "Sí" if item.revisiones else "No",
        ", ".join(dict.fromkeys(revisores)) or None,
    ]


def libro(
    nombre: str,
    recomendacion: Recomendacion,
    justificacion: str,
    items: list[ItemMatrizOut],
) -> bytes:
    """Libro con la matriz (una fila por ítem) y una hoja de resumen."""
    wb = Workbook()
    wb.remove(wb.worksheets[0])
    matriz = wb.create_sheet("Matriz")
    _escribir(matriz, 1, [titulo for titulo, _ in COLUMNAS])
    for indice, (_, ancho) in enumerate(COLUMNAS, start=1):
        matriz.column_dimensions[matriz.cell(row=1, column=indice).column_letter].width = ancho
        matriz.cell(row=1, column=indice).font = Font(bold=True)
    matriz.freeze_panes = "A2"
    for fila, item in enumerate(items, start=2):
        _escribir(matriz, fila, _fila(item))

    resumen = wb.create_sheet("Resumen")
    conteos = {estado: sum(1 for i in items if i.estado == estado) for estado in EstadoMatriz}
    filas: list[list[str | int | None]] = [
        ["Licitación", nombre],
        ["Recomendación", RECOMENDACIONES[recomendacion]],
        ["Justificación", justificacion],
        ["Ítems", len(items)],
    ]
    filas += [[ESTADOS[estado], conteo] for estado, conteo in conteos.items()]
    for fila, valores in enumerate(filas, start=1):
        _escribir(resumen, fila, valores)
        resumen.cell(row=fila, column=1).font = Font(bold=True)
    resumen.column_dimensions["A"].width = 22
    resumen.column_dimensions["B"].width = 90

    salida = BytesIO()
    wb.save(salida)
    return salida.getvalue()
