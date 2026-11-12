"""HU-B19: prioridad, repetibilidad y referencias de la recomendación."""

import pytest

from analizador.contratos.decision import EstadoMatriz, JustificacionGenerada, Recomendacion
from analizador.models.enums import TipoRequisito
from analizador.pipeline.cobertura import SeccionNoProcesada
from analizador.pipeline.decision import ItemDecision, evaluar, formatear_justificacion
from analizador.pipeline.errores import AnalisisError

HABILITANTE = ItemDecision(TipoRequisito.HABILITANTE, EstadoMatriz.NO_CUMPLE, "2.1")
REVISION = ItemDecision(TipoRequisito.TECNICO, EstadoMatriz.REVISION, "3.2")
CONFORME = ItemDecision(TipoRequisito.HABILITANTE, EstadoMatriz.CUMPLE, "2.3")
PENDIENTE = SeccionNoProcesada("4.1", None, 4, 5, "Falta procesar")


@pytest.mark.parametrize(
    ("items", "pendientes", "esperada", "referencias"),
    [
        ([], [], Recomendacion.CONDICIONADA, ()),
        ([HABILITANTE, REVISION], [PENDIENTE], Recomendacion.CONDICIONADA, ("3.2", "4.1")),
        ([HABILITANTE], [PENDIENTE], Recomendacion.CONDICIONADA, ("4.1",)),
        ([HABILITANTE, REVISION], [], Recomendacion.CONDICIONADA, ("3.2",)),
        ([HABILITANTE], [], Recomendacion.NO_PRESENTARSE, ("2.1",)),
        ([CONFORME], [], Recomendacion.PRESENTARSE, ("2.3",)),
    ],
)
def test_precedencia_de_recomendacion(
    items: list[ItemDecision],
    pendientes: list[SeccionNoProcesada],
    esperada: Recomendacion,
    referencias: tuple[str, ...],
) -> None:
    decision = evaluar(items, pendientes)
    assert decision.recomendacion == esperada
    assert decision.numerales == referencias
    assert decision == evaluar(items, pendientes)


@pytest.mark.parametrize("estado", [EstadoMatriz.NO_CUMPLE, EstadoMatriz.CUMPLE_PARCIALMENTE])
@pytest.mark.parametrize(
    ("categoria", "esperada"),
    [
        (TipoRequisito.HABILITANTE, Recomendacion.NO_PRESENTARSE),
        (TipoRequisito.EXPERIENCIA, Recomendacion.PRESENTARSE_CON_RIESGO),
    ],
)
def test_una_brecha_depende_de_la_categoria(
    estado: EstadoMatriz, categoria: TipoRequisito, esperada: Recomendacion
) -> None:
    assert evaluar([ItemDecision(categoria, estado, "3.1")], []).recomendacion == esperada


@pytest.mark.parametrize(
    ("texto", "referencias"),
    [("Referencia inexistente", [2]), ("Sin referencias", []), ("Véase numeral 9.1", [1])],
)
def test_la_redaccion_no_puede_citar_numerales_ajenos(texto: str, referencias: list[int]) -> None:
    decision = evaluar([HABILITANTE], [])
    with pytest.raises(AnalisisError, match="no se pudo comprobar"):
        formatear_justificacion(
            decision, JustificacionGenerada(texto=texto, referencias=referencias)
        )


def test_las_citas_se_renderizan_desde_los_numerales_persistidos() -> None:
    decision = evaluar([HABILITANTE], [])
    texto = formatear_justificacion(
        decision, JustificacionGenerada(texto="Falta acreditar un requisito.", referencias=[1, 1])
    )
    assert "Numerales del pliego: 2.1." in texto
    assert texto.count("2.1") == 1
