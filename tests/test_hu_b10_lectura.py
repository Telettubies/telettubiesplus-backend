"""HU-B10: leer el texto del pliego, incluidas páginas escaneadas (RF-16, ADR-010, P-09).

Los PDF se generan en la propia prueba. Tesseract se sustituye por un doble salvo en la
prueba marcada como real, que se omite si el idioma español no está instalado.
"""

from typing import Any

import pytesseract
import pytest

from analizador.core.config import get_settings
from analizador.models.enums import OrigenPagina
from analizador.pipeline import lectura
from analizador.pipeline.errores import AnalisisError
from tests.apoyo_pdf import PaginaDePrueba, Tabla, crear_pdf, pdf_protegido

TABLA = Tabla(
    [
        ["Indicador", "Exigido"],
        ["Índice de liquidez", "Mayor o igual a 1,5"],
        ["Endeudamiento", "Menor o igual a 70 %"],
    ]
)
TEXTO_OCR = "El proponente debe acreditar experiencia general de cinco años"


class OcrFalso:
    """Sustituye a `lectura._ocr`: devuelve lo configurado y anota cuántas veces se usó."""

    def __init__(self, texto: str = TEXTO_OCR, confianza: float = 91.0) -> None:
        self.texto, self.confianza = texto, confianza
        self.llamadas = 0

    def __call__(self, imagen_png: bytes, settings: Any) -> tuple[str, list[dict[str, Any]], float]:
        assert imagen_png.startswith(b"\x89PNG")
        self.llamadas += 1
        palabras = [
            {"t": palabra, "x0": 0.1 + i * 0.05, "y0": 0.2, "x1": 0.14 + i * 0.05, "y1": 0.22}
            for i, palabra in enumerate(self.texto.split())
        ]
        return self.texto, palabras, self.confianza


class VisionFalsa:
    def __init__(
        self, respuesta: str | None = "Texto transcrito por el modelo con visión."
    ) -> None:
        self.respuesta = respuesta
        self.imagenes: list[bytes] = []

    async def __call__(self, imagen_png: bytes) -> str | None:
        self.imagenes.append(imagen_png)
        return self.respuesta


@pytest.fixture
def ocr(monkeypatch: pytest.MonkeyPatch) -> OcrFalso:
    falso = OcrFalso()
    monkeypatch.setattr(lectura, "_ocr", falso)
    return falso


# --- Criterio 1: texto por página con su número, y tablas como texto ordenado ----------------


async def test_cada_pagina_trae_su_texto_y_su_numero() -> None:
    pdf = crear_pdf(
        [
            ["1. INFORMACIÓN GENERAL", "El presente proceso tiene por objeto contratar servicios."],
            ["2. REQUISITOS HABILITANTES", "El proponente deberá acreditar capacidad jurídica."],
            ["3. CRONOGRAMA", "El cierre del proceso será el 30 de noviembre de 2026."],
        ]
    )

    paginas = await lectura.leer_pdf(pdf)

    assert [p.numero for p in paginas] == [1, 2, 3]
    assert all(p.origen == OrigenPagina.DIGITAL for p in paginas)
    assert "INFORMACIÓN GENERAL" in paginas[0].texto
    assert "capacidad jurídica" in paginas[1].texto
    assert "30 de noviembre de 2026" in paginas[2].texto
    assert "REQUISITOS HABILITANTES" not in paginas[0].texto


async def test_una_tabla_se_convierte_en_filas_ordenadas_en_su_lugar() -> None:
    pdf = crear_pdf(
        [
            PaginaDePrueba(
                [
                    "3.2 CAPACIDAD FINANCIERA",
                    "El proponente deberá cumplir los siguientes indicadores:",
                    TABLA,
                    "Los indicadores se verifican con el RUP vigente.",
                ]
            )
        ]
    )

    [pagina] = await lectura.leer_pdf(pdf)

    lineas = pagina.texto.splitlines()
    assert "Indicador | Exigido" in lineas
    assert "Índice de liquidez | Mayor o igual a 1,5" in lineas
    assert "Endeudamiento | Menor o igual a 70 %" in lineas
    # En su lugar: después del texto que la introduce y antes del que la sigue.
    assert (
        lineas.index("El proponente deberá cumplir los siguientes indicadores:")
        < lineas.index("Indicador | Exigido")
        < lineas.index("Endeudamiento | Menor o igual a 70 %")
        < lineas.index("Los indicadores se verifican con el RUP vigente.")
    )
    # Sin duplicar: el contenido de la tabla no aparece además como texto suelto.
    assert pagina.texto.count("Índice de liquidez") == 1
    assert pagina.texto.count("Mayor o igual a 1,5") == 1


def test_las_celdas_vacias_y_los_saltos_de_linea_no_rompen_la_fila() -> None:
    texto = lectura._tabla_como_texto(
        [["Perfil", "Experiencia\nmínima"], ["Director", None], [None, None]]
    )
    assert texto == "Perfil | Experiencia mínima\nDirector | "


# --- Completitud: el número de páginas leídas coincide con el del PDF ------------------------


async def test_se_leen_todas_las_paginas_del_pdf(ocr: OcrFalso) -> None:
    pdf = crear_pdf(
        [
            ["Página digital con suficiente texto para no ser escaneada."],
            PaginaDePrueba([TEXTO_OCR], escaneada=True),
            [],  # página en blanco
            ["Otra página digital con suficiente texto para leerse bien."],
        ]
    )

    paginas = await lectura.leer_pdf(pdf)

    assert [p.numero for p in paginas] == [1, 2, 3, 4]


async def test_un_pliego_con_mas_paginas_que_el_maximo_se_rechaza_indicando_el_limite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAX_PDF_PAGINAS", "2")
    get_settings.cache_clear()
    pdf = crear_pdf([["Página con texto suficiente."] for _ in range(3)])

    with pytest.raises(AnalisisError) as exc:
        await lectura.leer_pdf(pdf)

    assert exc.value.mensaje_usuario == ("El pliego tiene 3 páginas y el máximo permitido es 2.")


async def test_un_pdf_protegido_o_danado_da_un_mensaje_claro() -> None:
    with pytest.raises(AnalisisError, match="contraseña"):
        await lectura.leer_pdf(pdf_protegido())
    with pytest.raises(AnalisisError, match="dañado"):
        await lectura.leer_pdf(b"%PDF-1.7\nesto no es un pdf de verdad")


# --- Criterio 2: una página sin texto extraíble pasa por OCR ---------------------------------


async def test_una_pagina_escaneada_pasa_por_ocr_y_solo_ella(ocr: OcrFalso) -> None:
    pdf = crear_pdf(
        [
            ["Página digital con suficiente texto para no ser escaneada."],
            PaginaDePrueba([TEXTO_OCR], escaneada=True),
            ["Otra página digital con suficiente texto para leerse bien."],
        ]
    )

    paginas = await lectura.leer_pdf(pdf)

    assert [p.origen for p in paginas] == [
        OrigenPagina.DIGITAL,
        OrigenPagina.OCR,
        OrigenPagina.DIGITAL,
    ]
    assert paginas[1].texto == TEXTO_OCR
    assert ocr.llamadas == 1  # el OCR solo se aplica a la página sin texto


async def test_una_pagina_en_blanco_no_se_manda_a_ocr(ocr: OcrFalso) -> None:
    [pagina] = await lectura.leer_pdf(crear_pdf([[]]))

    assert pagina.origen == OrigenPagina.DIGITAL and pagina.texto == ""
    assert ocr.llamadas == 0


# --- Criterio 4: en páginas leídas por OCR se conservan las coordenadas ----------------------


async def test_el_ocr_conserva_la_caja_de_cada_palabra(ocr: OcrFalso) -> None:
    [pagina] = await lectura.leer_pdf(crear_pdf([PaginaDePrueba([TEXTO_OCR], escaneada=True)]))

    assert pagina.palabras is not None
    assert [p["t"] for p in pagina.palabras] == TEXTO_OCR.split()
    for palabra in pagina.palabras:
        assert 0 <= palabra["x0"] < palabra["x1"] <= 1
        assert 0 <= palabra["y0"] < palabra["y1"] <= 1


async def test_las_paginas_digitales_no_guardan_cajas() -> None:
    [pagina] = await lectura.leer_pdf(crear_pdf([["Página digital con texto suficiente."]]))
    assert pagina.palabras is None


def _tesseract_con_espanol() -> bool:
    try:
        return "spa" in pytesseract.get_languages(config="")
    except Exception:
        return False


@pytest.mark.skipif(not _tesseract_con_espanol(), reason="Tesseract sin el idioma español (spa)")
async def test_tesseract_real_lee_una_pagina_escaneada_con_sus_coordenadas() -> None:
    pdf = crear_pdf(
        [
            PaginaDePrueba(
                [
                    "REQUISITOS HABILITANTES",
                    "El proponente debe acreditar experiencia general",
                    "de cinco años en contratos de consultoria.",
                ],
                escaneada=True,
                tamano_letra=16,
            )
        ]
    )

    [pagina] = await lectura.leer_pdf(pdf)

    assert pagina.origen == OrigenPagina.OCR
    assert "experiencia" in pagina.texto and "HABILITANTES" in pagina.texto
    assert pagina.palabras is not None
    [experiencia] = [p for p in pagina.palabras if p["t"] == "experiencia"]
    # La palabra está en el tercio superior de la página y hacia la derecha del margen.
    assert 0.05 < experiencia["y0"] < 0.3
    assert 0.3 < experiencia["x0"] < 0.9
    assert experiencia["x1"] > experiencia["x0"] and experiencia["y1"] > experiencia["y0"]


# --- Criterio 3: si el OCR no logra texto legible, visión; si tampoco, no leída --------------


async def test_si_el_ocr_no_es_legible_se_usa_el_modelo_con_vision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(lectura, "_ocr", OcrFalso("x~ ;; ¬", confianza=12.0))
    vision = VisionFalsa("El contratista deberá constituir una póliza de cumplimiento.")
    pdf = crear_pdf([PaginaDePrueba(["texto borroso"], escaneada=True)])

    [pagina] = await lectura.leer_pdf(pdf, vision)

    assert pagina.origen == OrigenPagina.VISION
    assert pagina.texto == "El contratista deberá constituir una póliza de cumplimiento."
    assert pagina.palabras is None  # la visión no da coordenadas
    assert len(vision.imagenes) == 1 and vision.imagenes[0].startswith(b"\x89PNG")


@pytest.mark.parametrize(
    "ocr_falso",
    [
        pytest.param(OcrFalso("poco", confianza=95.0), id="muy-poco-texto"),
        pytest.param(OcrFalso(TEXTO_OCR, confianza=30.0), id="confianza-baja"),
    ],
)
async def test_que_cuenta_como_ilegible(
    monkeypatch: pytest.MonkeyPatch, ocr_falso: OcrFalso
) -> None:
    monkeypatch.setattr(lectura, "_ocr", ocr_falso)
    vision = VisionFalsa()

    [pagina] = await lectura.leer_pdf(
        crear_pdf([PaginaDePrueba(["texto"], escaneada=True)]), vision
    )

    assert pagina.origen == OrigenPagina.VISION


@pytest.mark.parametrize(
    "vision",
    [
        pytest.param(VisionFalsa(None), id="vision-no-logra-leer"),
        pytest.param(None, id="sin-modelo-con-vision"),
    ],
)
async def test_si_tampoco_hay_texto_la_pagina_queda_marcada_como_no_leida(
    monkeypatch: pytest.MonkeyPatch, vision: VisionFalsa | None
) -> None:
    monkeypatch.setattr(lectura, "_ocr", OcrFalso("", confianza=0.0))
    pdf = crear_pdf(
        [
            ["Página digital con suficiente texto para leerse bien."],
            PaginaDePrueba(["ilegible"], escaneada=True),
        ]
    )

    paginas = await lectura.leer_pdf(pdf, vision)

    # No se omite en silencio: la página existe, sin texto y marcada.
    assert [p.origen for p in paginas] == [OrigenPagina.DIGITAL, OrigenPagina.NO_LEIDA]
    assert paginas[1].numero == 2 and paginas[1].texto == ""


async def test_un_fallo_del_modelo_con_vision_no_tumba_la_lectura(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(lectura, "_ocr", OcrFalso("", confianza=0.0))

    async def vision_caida(_imagen: bytes) -> str | None:
        raise ConnectionError("proveedor caído")

    [pagina] = await lectura.leer_pdf(
        crear_pdf([PaginaDePrueba(["ilegible"], escaneada=True)]), vision_caida
    )

    assert pagina.origen == OrigenPagina.NO_LEIDA


async def test_sin_tesseract_instalado_se_pasa_directamente_a_vision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def sin_tesseract(*_args: Any, **_kwargs: Any) -> Any:
        raise pytesseract.TesseractNotFoundError()

    monkeypatch.setattr(pytesseract, "image_to_data", sin_tesseract)
    vision = VisionFalsa()

    [pagina] = await lectura.leer_pdf(
        crear_pdf([PaginaDePrueba(["texto"], escaneada=True)]), vision
    )

    assert pagina.origen == OrigenPagina.VISION


async def test_el_numero_de_paginas_enviadas_a_vision_tiene_un_tope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OCR_VISION_MAX_PAGINAS", "2")
    get_settings.cache_clear()
    monkeypatch.setattr(lectura, "_ocr", OcrFalso("", confianza=0.0))
    vision = VisionFalsa()
    pdf = crear_pdf([PaginaDePrueba(["ilegible"], escaneada=True) for _ in range(4)])

    paginas = await lectura.leer_pdf(pdf, vision)

    assert len(vision.imagenes) == 2
    assert [p.origen for p in paginas] == [
        OrigenPagina.VISION,
        OrigenPagina.VISION,
        OrigenPagina.NO_LEIDA,
        OrigenPagina.NO_LEIDA,
    ]
