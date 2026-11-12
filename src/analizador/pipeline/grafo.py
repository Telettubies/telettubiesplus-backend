"""Grafo de LangGraph del análisis de un pliego (ADR-007, F-03).

Lineal (etapas 1 → 11). Con el checkpointer de PostgreSQL (P-08) cada etapa completada queda
guardada con thread_id = trabajo_id; si el trabajo se vuelve a ejecutar, el grafo continúa
desde la última etapa completada en vez de repetir llamadas ya pagadas a los modelos.
"""

from collections.abc import Sequence
from typing import Any, cast

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from analizador.pipeline.estado import EstadoAnalisis
from analizador.pipeline.etapas import ETAPAS, Nodo


def construir(
    checkpointer: BaseCheckpointSaver[Any] | None = None,
    etapas: Sequence[tuple[str, Nodo]] = ETAPAS,
) -> Any:
    grafo = StateGraph(EstadoAnalisis)
    anterior = START
    for nombre, nodo in etapas:
        # Los nodos async son válidos; los overloads de LangGraph no infieren el TypedDict.
        grafo.add_node(nombre, cast(Any, nodo))
        grafo.add_edge(anterior, nombre)
        anterior = nombre
    grafo.add_edge(anterior, END)
    return grafo.compile(checkpointer=checkpointer)


async def ejecutar(grafo: Any, estado: EstadoAnalisis) -> None:
    """Ejecuta el análisis del trabajo, o lo reanuda si ya había empezado.

    - Sin checkpoint previo: empieza desde la primera etapa.
    - Con una etapa pendiente (falló o se interrumpió): continúa desde ella.
    - Ya terminado: no hace nada, para que una entrega duplicada no lo repita (HU-B09).
    """
    config = {"configurable": {"thread_id": estado["trabajo_id"]}}
    entrada: EstadoAnalisis | None = estado
    if grafo.checkpointer is not None:
        previo = await grafo.aget_state(config)
        if previo.next:
            entrada = None  # reanudar desde el último checkpoint
        elif previo.created_at is not None:
            return  # el grafo de este trabajo ya terminó
    await grafo.ainvoke(entrada, config)
