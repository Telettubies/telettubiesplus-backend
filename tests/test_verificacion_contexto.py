"""Contexto del evaluador y exigibilidad de los ítems (HU-B13, HU-B17).

- El evaluador recibe la base completa agrupada por entidad y el texto de los numerales que
  cita el requisito, para que no concluya que falta lo que está en otra parte.
- Un ítem numérico siempre es histórico: compara datos de la empresa.
"""

import uuid
from typing import Any

from analizador.contratos.extraccion import (
    CampoComparable,
    ExtraccionSeccion,
    ItemExtraido,
    Operador,
    ParametrosNumericos,
    RequisitoExtraido,
    TipoExigibilidad,
    TipoVerificacion,
    Unidad,
)
from analizador.models import Item, Requisito
from analizador.models.enums import TipoRequisito
from analizador.pipeline.extraccion import normalizar
from analizador.pipeline.verificacion import (
    Evidencia,
    SeccionContexto,
    _mensaje,
    contexto_pliego,
    numerales_citados,
)

SECCION_PROPIA = uuid.uuid4()
SECCIONES = [
    SeccionContexto(SECCION_PROPIA, "7", "Experiencia", "El proponente acreditará experiencia."),
    SeccionContexto(uuid.uuid4(), "4", "Objeto", "Objeto: plataforma documental."),
    SeccionContexto(uuid.uuid4(), "4.2", "Alcance", "Migración de archivos e indexación."),
    SeccionContexto(uuid.uuid4(), "40", "Otro", "No debe aparecer."),
]


def _item(literal: str, descripcion: str = "Condición") -> Item:
    requisito = Requisito(
        seccion_id=SECCION_PROPIA,
        numeral="7.1",
        tipo=TipoRequisito.EXPERIENCIA,
        texto_literal=literal,
    )
    item = Item(descripcion=descripcion, orden=1)
    item.requisito = requisito
    return item


def _candidato(fuente: str, texto: str, tipo: str = "contrato") -> dict[str, Any]:
    return {"fuente_id": fuente, "fuente": fuente, "tipo_fuente": tipo, "texto": texto}


def test_numerales_citados() -> None:
    assert numerales_citados("según el numeral 4 y los numerales 5.1, 5.2 y 6") == [
        "4",
        "5.1",
        "5.2",
        "6",
    ]
    assert numerales_citados("experiencia de 4 años en 2 contratos") == []


def test_el_contexto_incluye_la_seccion_propia_y_las_citadas_con_sus_subnumerales() -> None:
    item = _item("Experiencia en el objeto descrito en el numeral 4.")

    contexto = contexto_pliego(item.requisito, item, SECCIONES, 10_000)

    assert "acreditará experiencia" in contexto
    assert "plataforma documental" in contexto and "indexación" in contexto
    assert "No debe aparecer" not in contexto


def test_el_contexto_del_pliego_respeta_el_maximo() -> None:
    item = _item("Ver numeral 4.")

    assert len(contexto_pliego(item.requisito, item, SECCIONES, 20)) < 200
    assert contexto_pliego(item.requisito, item, SECCIONES, 0) == ""


def test_la_base_completa_va_agrupada_por_entidad_y_marcada_para_la_cache() -> None:
    item = _item("Experiencia en gestión documental.")
    evidencia = Evidencia(
        [
            _candidato("C-1", "Contrato C-1: desarrollo web."),
            _candidato("C-1", "Migración de 1,2 millones de documentos."),
            _candidato("Ana", "Ana, ingeniera de sistemas.", "hoja_de_vida"),
        ],
        completa=True,
    )

    bloques = _mensaje(item, evidencia, "<seccion>texto</seccion>")

    perfil = bloques[0]["text"]
    assert bloques[0].get("cache_control") == {"type": "ephemeral"}
    assert '<evidencia completa="si">' in perfil
    assert perfil.count("<entidad ") == 2 and perfil.count("</entidad>") == 2
    assert '<fragmento numero="3">' in perfil
    assert (
        "<pliego>" in bloques[1]["text"]
        and "Experiencia en gestión documental" in (bloques[1]["text"])
    )


def test_los_fragmentos_recuperados_no_se_marcan_para_la_cache() -> None:
    item = _item("Experiencia.")
    bloques = _mensaje(item, Evidencia([_candidato("C-1", "texto")], completa=False))

    assert "cache_control" not in bloques[0]
    assert '<evidencia completa="no">' in bloques[0]["text"]
    assert "<pliego>" not in bloques[1]["text"]


def test_un_item_numerico_siempre_es_historico() -> None:
    extraccion = ExtraccionSeccion(
        requisitos=[
            RequisitoExtraido(
                numeral="3.1",
                tipo=TipoRequisito.HABILITANTE,
                texto_literal="Índice de liquidez mayor o igual a 1,5. Radicar factura mensual.",
                ambiguo=False,
                items=[
                    ItemExtraido(
                        descripcion="Índice de liquidez >= 1,5",
                        tipo_exigibilidad=TipoExigibilidad.DOCUMENTO_OFERTA,
                        tipo_verificacion=TipoVerificacion.NUMERICO,
                        parametros=ParametrosNumericos(
                            campo=CampoComparable.INDICE_LIQUIDEZ,
                            operador=Operador.MAYOR_O_IGUAL,
                            valor=1.5,
                            unidad=Unidad.RAZON,
                        ),
                    ),
                    ItemExtraido(
                        descripcion="Radicar factura electrónica cada mes",
                        tipo_exigibilidad=TipoExigibilidad.COMPROMISO_EJECUCION,
                        tipo_verificacion=TipoVerificacion.CUALITATIVO,
                    ),
                ],
            )
        ]
    )

    items = normalizar(extraccion)[0].items

    assert items[0].tipo_exigibilidad == TipoExigibilidad.HISTORICO_EMPRESA
    assert items[1].tipo_exigibilidad == TipoExigibilidad.COMPROMISO_EJECUCION


def test_sin_exigibilidad_el_item_se_evalua_contra_la_base() -> None:
    item = ItemExtraido(descripcion="x", tipo_verificacion=TipoVerificacion.CUALITATIVO)

    assert item.tipo_exigibilidad == TipoExigibilidad.HISTORICO_EMPRESA
