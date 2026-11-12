"""Envío a revisión de ítems dudosos contra PostgreSQL (HU-B20)."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from analizador.core.config import get_settings
from analizador.models import Resultado, Revision
from analizador.models.enums import EstadoCumplimiento
from analizador.pipeline import citas
from tests.apoyo_analisis import agregar_requisito, crear_analisis


async def test_revision_reune_motivos_y_respeta_el_umbral_y_la_revision_humana(
    sesion: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "revision_umbral_confianza", 0.8)
    analisis = await crear_analisis(sesion)
    ambiguo = await agregar_requisito(
        sesion,
        analisis,
        numeral="2.1",
        texto="Texto de la sección del pliego.",
        items=["dudoso"],
        ambiguo=True,
        motivo_ambiguedad="Admite dos lecturas.",
    )
    claro = await agregar_requisito(
        sesion,
        analisis,
        numeral="2.2",
        texto="Texto de la sección del pliego.",
        items=["en el umbral", "validado por un revisor"],
    )
    dudoso = Resultado(
        item_id=ambiguo.items[0].id,
        estado=EstadoCumplimiento.CUMPLE,
        confianza=0.79,
        evidencia=[{"tipo": "cita", "cita": "Afirmación ajena", "texto": "Referencia comprobable"}],
    )
    limite = Resultado(
        item_id=claro.items[0].id, estado=EstadoCumplimiento.NO_CUMPLE, confianza=0.8
    )
    revisado = Resultado(item_id=claro.items[1].id, estado=EstadoCumplimiento.CUMPLE, confianza=0.1)
    revisado.revisiones.append(
        Revision(
            usuario_id=analisis.quien.usuario_id,
            estado_anterior=EstadoCumplimiento.NO_CUMPLE,
            estado_nuevo=EstadoCumplimiento.CUMPLE,
            comentario="Soporte contrastado por una persona.",
        )
    )
    sesion.add_all([dudoso, limite, revisado])
    await sesion.commit()
    await citas.ejecutar(analisis.estado)
    await citas.ejecutar(analisis.estado)
    for resultado in (dudoso, limite, revisado):
        await sesion.refresh(resultado)
    assert dudoso.requiere_revision
    assert (dudoso.motivo_revision or "").split(" | ") == [
        "Admite dos lecturas.",
        citas.MOTIVO_CITA_NO_VERIFICADA,
        citas.MOTIVO_CONFIANZA_BAJA,
    ]
    assert dudoso.estado == EstadoCumplimiento.CUMPLE
    assert not limite.requiere_revision
    assert not revisado.requiere_revision
