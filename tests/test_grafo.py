"""El grafo de análisis recorre las etapas de la sección 5.3 en orden (sin base de datos).

Las etapas reales necesitan PostgreSQL y el almacén; aquí se sustituyen por nodos vacíos para
probar solo el cableado del grafo y el registro de avance. El comportamiento completo del
worker está en tests/integracion/test_hu_b09_orquestacion.py.
"""

import uuid
from typing import Any

import pytest

from analizador.pipeline import catalogo, etapas, grafo, progreso
from analizador.pipeline.estado import EstadoAnalisis


async def test_grafo_ejecuta_todas_las_etapas_en_orden(monkeypatch: pytest.MonkeyPatch) -> None:
    registradas: list[tuple[str, int]] = []

    async def registrar_etapa(_trabajo_id: uuid.UUID, etapa: str, porcentaje: int) -> None:
        registradas.append((etapa, porcentaje))

    monkeypatch.setattr(progreso, "registrar_etapa", registrar_etapa)

    def nodo_vacio(clave: str) -> etapas.Nodo:
        @etapas.etapa(clave)
        async def nodo(_estado: EstadoAnalisis) -> dict[str, Any]:
            return {}

        return nodo

    estado = EstadoAnalisis(
        trabajo_id=str(uuid.uuid4()),
        licitacion_id=str(uuid.uuid4()),
        organizacion_id=str(uuid.uuid4()),
    )
    nodos = [(e.clave, nodo_vacio(e.clave)) for e in catalogo.ETAPAS]
    await grafo.ejecutar(grafo.construir(etapas=nodos), estado)

    assert registradas == [(e.clave, e.porcentaje) for e in catalogo.ETAPAS]
    assert registradas[-1] == ("cierre", 100)


def test_cada_etapa_del_catalogo_tiene_su_nodo() -> None:
    assert [clave for clave, _ in etapas.ETAPAS] == [e.clave for e in catalogo.ETAPAS]
