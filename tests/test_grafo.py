"""El grafo de análisis recorre las etapas de la sección 5.3 en orden."""

import uuid

import pytest

from analizador.pipeline import grafo, progreso
from analizador.pipeline.estado import EstadoAnalisis
from analizador.pipeline.etapas import ETAPAS


async def test_grafo_ejecuta_todas_las_etapas_en_orden(monkeypatch: pytest.MonkeyPatch) -> None:
    registradas: list[tuple[str, int]] = []

    async def registrar_etapa(_trabajo_id: uuid.UUID, etapa: str, porcentaje: int) -> None:
        registradas.append((etapa, porcentaje))

    async def marcar_licitacion(*_args: object) -> None:
        return None

    monkeypatch.setattr(progreso, "registrar_etapa", registrar_etapa)
    monkeypatch.setattr(progreso, "marcar_licitacion", marcar_licitacion)

    estado = EstadoAnalisis(
        trabajo_id=str(uuid.uuid4()),
        licitacion_id=str(uuid.uuid4()),
        organizacion_id=str(uuid.uuid4()),
    )
    await grafo.ejecutar(grafo.construir(), estado)

    assert [nombre for nombre, _ in registradas] == [nombre for nombre, _ in ETAPAS]
    porcentajes = [p for _, p in registradas]
    assert porcentajes == sorted(porcentajes)
    assert porcentajes[-1] == 100
