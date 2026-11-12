"""HU-B11: dividir el pliego en secciones con su numeral (RF-02, ADR-012).

No hay un pliego real de SECOP en el repositorio (Q-11): se usa uno sintético con la estructura
habitual de un pliego tipo, incluida su tabla de contenido.
"""

import pytest

from analizador.pipeline.secciones import PREAMBULO, SeccionDetectada, dividir

# Tabla de contenido del pliego sintético: numeral → título.
INDICE = {
    "CAPÍTULO I": "INFORMACIÓN GENERAL",
    "1.1": "Objeto del proceso",
    "1.2": "Presupuesto oficial",
    "CAPÍTULO II": "REQUISITOS HABILITANTES",
    "2.1": "Capacidad jurídica",
    "2.1.1": "Consorcios y uniones temporales",
    "2.2": "Capacidad financiera",
    "2.3": "Experiencia del proponente",
    "CAPÍTULO III": "CRONOGRAMA",
    "3.1": "Plazo de ejecución",
    "ANEXO 1": "CARTA DE PRESENTACIÓN",
}

PAGINAS = [
    (
        1,
        "ALCALDÍA MUNICIPAL (dato sintético)\n"
        "PLIEGO DE CONDICIONES DEFINITIVO\n"
        "LICITACIÓN PÚBLICA LP-004-2026\n\n"
        "TABLA DE CONTENIDO\n"
        + "\n".join(
            f"{numeral} {titulo} ........ {n + 2}"
            for n, (numeral, titulo) in enumerate(INDICE.items())
        ),
    ),
    (
        2,
        "CAPÍTULO I\nINFORMACIÓN GENERAL\n"
        "La entidad invita a los interesados a participar en el presente proceso.\n"
        "1.1 Objeto del proceso\n"
        "Contratar el desarrollo e implementación de un sistema de gestión documental.\n"
        "1.2 Presupuesto oficial\n"
        "El presupuesto oficial es de 2.500 millones de pesos, equivalentes a\n"
        "1.923 SMMLV del año 2026.",
    ),
    (
        3,
        "CAPÍTULO II\nREQUISITOS HABILITANTES\n"
        "2.1 Capacidad jurídica\n"
        "El proponente deberá aportar los siguientes documentos:\n"
        "1. Certificado de existencia y representación legal.\n"
        "2. Fotocopia del documento del representante legal.\n"
        "2.1.1. Consorcios y uniones temporales\n"
        "Deberán aportar el documento de constitución, según el anexo 1 del pliego.\n"
        "2.2 Capacidad financiera\n"
        "Índice de liquidez mayor o igual a 1.5 y endeudamiento menor o igual al 70 %.",
    ),
    (
        4,
        "El capital de trabajo deberá ser igual o superior al 30 % del presupuesto.\n"
        "2.3 Experiencia del proponente\n"
        "Acreditar hasta tres contratos cuyo valor sumado sea igual o superior a\n"
        "2.000 SMMLV, clasificados en el código UNSPSC 81111500.",
    ),
    (
        5,
        "CAPÍTULO III - CRONOGRAMA\n"
        "3.1 Plazo de ejecución\n"
        "El plazo de ejecución será de ocho (8) meses contados desde el acta de inicio.\n"
        "ANEXO 1: CARTA DE PRESENTACIÓN\n"
        "Ciudad y fecha. Señores Alcaldía Municipal.",
    ),
]
TEXTO = "\n".join(texto for _, texto in PAGINAS)


@pytest.fixture(scope="module")
def secciones() -> list[SeccionDetectada]:
    return dividir(PAGINAS, maximo=12000)


def _por_numeral(secciones: list[SeccionDetectada], numeral: str) -> SeccionDetectada:
    [seccion] = [s for s in secciones if s.numeral == numeral]
    return seccion


# --- Criterio 1: cada encabezado abre una sección con numeral, título y páginas --------------


def test_los_numerales_detectados_coinciden_con_la_tabla_de_contenido(
    secciones: list[SeccionDetectada],
) -> None:
    detectados = {s.numeral: s.titulo for s in secciones if s.numeral is not None}
    assert detectados == INDICE
    # Y en el mismo orden en que aparecen en el índice.
    assert [s.numeral for s in secciones if s.numeral is not None] == list(INDICE)


def test_reconoce_numerales_jerarquicos_y_palabras_de_division(
    secciones: list[SeccionDetectada],
) -> None:
    assert _por_numeral(secciones, "2.1.1").titulo == "Consorcios y uniones temporales"
    # El título de un capítulo puede venir en la línea siguiente o en la misma.
    assert _por_numeral(secciones, "CAPÍTULO II").titulo == "REQUISITOS HABILITANTES"
    assert _por_numeral(secciones, "CAPÍTULO III").titulo == "CRONOGRAMA"
    assert _por_numeral(secciones, "ANEXO 1").titulo == "CARTA DE PRESENTACIÓN"


def test_cada_seccion_conoce_sus_paginas(secciones: list[SeccionDetectada]) -> None:
    financiera = _por_numeral(secciones, "2.2")
    # Empieza en la página 3 y su último párrafo está en la 4.
    assert (financiera.pagina_inicio, financiera.pagina_fin) == (3, 4)
    assert "capital de trabajo" in financiera.texto

    objeto = _por_numeral(secciones, "1.1")
    assert (objeto.pagina_inicio, objeto.pagina_fin) == (2, 2)
    assert [s.orden for s in secciones] == list(range(1, len(secciones) + 1))


def test_el_texto_de_la_seccion_incluye_su_encabezado_y_su_contenido(
    secciones: list[SeccionDetectada],
) -> None:
    experiencia = _por_numeral(secciones, "2.3")
    assert experiencia.texto.startswith("2.3 Experiencia del proponente\n")
    assert "81111500" in experiencia.texto


# --- Qué no es un encabezado -----------------------------------------------------------------


def test_la_tabla_de_contenido_no_abre_secciones(secciones: list[SeccionDetectada]) -> None:
    preambulo = secciones[0]
    assert preambulo.numeral is None and preambulo.titulo == PREAMBULO
    assert "TABLA DE CONTENIDO" in preambulo.texto
    assert "2.3 Experiencia del proponente ........ 9" in preambulo.texto


def test_los_items_de_una_lista_numerada_no_abren_secciones(
    secciones: list[SeccionDetectada],
) -> None:
    juridica = _por_numeral(secciones, "2.1")
    assert "1. Certificado de existencia y representación legal." in juridica.texto
    assert "2. Fotocopia del documento del representante legal." in juridica.texto


def test_cifras_y_menciones_en_mitad_de_frase_no_abren_secciones(
    secciones: list[SeccionDetectada],
) -> None:
    # "1.923 SMMLV…" y "2.000 SMMLV…" empiezan línea, pero no son títulos.
    assert "1.923 SMMLV" in _por_numeral(secciones, "1.2").texto
    assert "2.000 SMMLV" in _por_numeral(secciones, "2.3").texto
    # "según el anexo 1 del pliego" no es el encabezado del anexo.
    assert len([s for s in secciones if s.numeral == "ANEXO 1"]) == 1


@pytest.mark.parametrize(
    "linea",
    [
        "1. Carta de presentación de la propuesta debidamente firmada.",
        "30.11.2026 es la fecha de cierre del proceso",
        "2.5 millones de pesos por cada mes de retraso",
        "1.923 SMMLV del año 2026 como presupuesto oficial",
        "1.5 SMMLV por cada día de retraso en la entrega",
        "2.000 SMMLV, clasificados en el código UNSPSC 81111500",
        "3.2 el proponente que no cumpla será rechazado",
        "el capítulo IV describe las obligaciones",
        "Anexo técnico del contrato de prestación de servicios",
    ],
)
def test_lineas_que_no_son_encabezados(linea: str) -> None:
    [unica] = dividir([(1, f"Texto inicial del pliego.\n{linea}\nMás texto.")], maximo=12000)
    assert unica.numeral is None


@pytest.mark.parametrize(
    ("linea", "numeral", "titulo"),
    [
        ("4. OBLIGACIONES DEL CONTRATISTA", "4", "OBLIGACIONES DEL CONTRATISTA"),
        ("4 - OBLIGACIONES DEL CONTRATISTA", "4", "OBLIGACIONES DEL CONTRATISTA"),
        ("3.4.2 Experiencia específica", "3.4.2", "Experiencia específica"),
        ("  5.1. Garantía de seriedad", "5.1", "Garantía de seriedad"),
        ("TÍTULO II DISPOSICIONES GENERALES", "TÍTULO II", "DISPOSICIONES GENERALES"),
        ("Capítulo IV. CONDICIONES DEL CONTRATO", "CAPÍTULO IV", "CONDICIONES DEL CONTRATO"),
        ("FORMATO No. 3 EXPERIENCIA", "FORMATO 3", "EXPERIENCIA"),
        ("SECCIÓN A", "SECCIÓN A", None),
    ],
)
def test_formas_de_encabezado_reconocidas(linea: str, numeral: str, titulo: str | None) -> None:
    secciones = dividir([(1, f"Texto previo.\n{linea}\ncontenido de la sección")], maximo=12000)
    assert (secciones[-1].numeral, secciones[-1].titulo) == (numeral, titulo)


# --- Criterio 2: todo el texto pertenece a alguna sección -----------------------------------


def test_la_union_de_las_secciones_es_exactamente_el_texto_del_pliego(
    secciones: list[SeccionDetectada],
) -> None:
    assert "".join(s.texto for s in secciones) == TEXTO
    # Verificable por longitud, como pide la historia.
    assert sum(len(s.texto) for s in secciones) == len(TEXTO)


def test_el_texto_previo_al_primer_encabezado_forma_una_seccion_propia(
    secciones: list[SeccionDetectada],
) -> None:
    assert secciones[0].titulo == PREAMBULO
    assert secciones[0].texto.startswith("ALCALDÍA MUNICIPAL")
    assert (secciones[0].pagina_inicio, secciones[0].pagina_fin) == (1, 1)


def test_un_pliego_sin_encabezados_es_una_sola_seccion() -> None:
    [unica] = dividir([(1, "Texto corrido sin numerales."), (2, "Sigue el texto.")], maximo=12000)
    assert unica.numeral is None and unica.titulo == PREAMBULO
    assert (unica.pagina_inicio, unica.pagina_fin) == (1, 2)


def test_si_el_pliego_empieza_con_un_encabezado_no_hay_preambulo_vacio() -> None:
    secciones = dividir([(1, "\n1. OBJETO\nContratar servicios.")], maximo=12000)
    assert [s.numeral for s in secciones] == ["1"]
    assert "".join(s.texto for s in secciones) == "\n1. OBJETO\nContratar servicios."


def test_las_paginas_sin_texto_no_rompen_la_division() -> None:
    paginas = [(1, "1. OBJETO\nContratar."), (2, ""), (3, "2. PLAZO\nSeis meses.")]
    secciones = dividir(paginas, maximo=12000)
    assert [(s.numeral, s.pagina_inicio, s.pagina_fin) for s in secciones] == [
        ("1", 1, 1),
        ("2", 3, 3),
    ]
    assert "".join(s.texto for s in secciones) == "1. OBJETO\nContratar.\n\n2. PLAZO\nSeis meses."


def test_sin_paginas_no_hay_secciones() -> None:
    assert dividir([], maximo=12000) == []


# --- Secciones largas -----------------------------------------------------------------------


def test_una_seccion_muy_larga_se_parte_en_tramos_sin_perder_texto() -> None:
    parrafos = "\n\n".join(f"Párrafo {n}. " + "obligación del contratista " * 20 for n in range(30))
    texto = f"4. OBLIGACIONES DEL CONTRATISTA\n{parrafos}\n5. GARANTÍAS\nPóliza de cumplimiento."

    secciones = dividir([(1, texto)], maximo=3000)

    partes = [s for s in secciones if s.numeral == "4"]
    assert len(partes) > 1
    assert all(len(s.texto) <= 3000 for s in secciones)
    assert partes[0].titulo == f"OBLIGACIONES DEL CONTRATISTA (parte 1 de {len(partes)})"
    assert "".join(s.texto for s in secciones) == texto
    # Se corta entre párrafos, no a mitad de una frase.
    assert all(parte.texto.endswith("\n\n") for parte in partes[:-1])
    assert secciones[-1].numeral == "5"
