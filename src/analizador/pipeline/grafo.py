"""Grafo de LangGraph del análisis de un pliego (ADR-007, F-03).

Lineal por ahora (etapas 1 → 11). Con el checkpointer de PostgreSQL (P-08) cada etapa
completada queda guardada con thread_id = trabajo_id; si el trabajo se vuelve a ejecutar,
el grafo continúa desde la última etapa completada en vez de repetir llamadas a los modelos.
"""

from typing import Any, cast

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from analizador.pipeline.estado import EstadoAnalisis
from analizador.pipeline.etapas import ETAPAS


def construir(checkpointer: BaseCheckpointSaver[Any] | None = None) -> Any:
    grafo = StateGraph(EstadoAnalisis)
    anterior = START
    for nombre, nodo in ETAPAS:
        # Los stubs async son nodos válidos; los overloads de LangGraph no infieren el TypedDict.
        grafo.add_node(nombre, cast(Any, nodo))
        grafo.add_edge(anterior, nombre)
        anterior = nombre
    grafo.add_edge(anterior, END)
    return grafo.compile(checkpointer=checkpointer)


async def ejecutar(grafo: Any, estado: EstadoAnalisis) -> None:
    config = {"configurable": {"thread_id": estado["trabajo_id"]}}
    entrada: EstadoAnalisis | None = estado
    if grafo.checkpointer is not None:
        previo = await grafo.aget_state(config)
        if previo.next:
            entrada = None  # reanudar desde el último checkpoint
    await grafo.ainvoke(entrada, config)
