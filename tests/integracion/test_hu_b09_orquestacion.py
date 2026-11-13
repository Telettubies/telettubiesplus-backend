"""HU-B09: orquestar el pipeline y tolerar fallos (ADR-006, ADR-007, ADR-015, P-08, P-15).

Las etapas reales se sustituyen por etapas de prueba que registran su ejecución y pueden
fallar a pedido; el registro de progreso, el checkpointer y el manejo de mensajes son reales.
"""

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy.ext.asyncio import AsyncSession

from analizador.core.config import get_settings
from analizador.db.session import get_sessionmaker
from analizador.messaging import topologia
from analizador.models import Licitacion, TrabajoProcesamiento
from analizador.models.enums import EstadoLicitacion, EstadoTrabajo, TipoTrabajo
from analizador.pipeline import catalogo, etapas, grafo
from analizador.pipeline.errores import AnalisisError
from analizador.pipeline.estado import EstadoAnalisis
from analizador.worker import handlers
from analizador.worker import main as worker
from tests.apoyo import Identidad, MensajeFalso, crear_organizacion, mensaje_de

ORDEN_DAS = [
    "lectura_pdf",
    "division_secciones",
    "filtrado",
    "extraccion",
    "localizacion",
    "comprobacion_cobertura",
    "recuperacion_evidencia",
    "verificacion",
    "validacion_citas",
    "decision",
    "justificacion",
    "cierre",
]


@dataclass
class EtapasDePrueba:
    """Sustituye cada etapa por un nodo que anota su ejecución y falla si se le pide."""

    ejecutadas: list[str] = field(default_factory=list)
    fallos: dict[str, BaseException] = field(default_factory=dict)
    al_ejecutar: dict[str, Any] = field(default_factory=dict)

    def _nodo(self, clave: str) -> etapas.Nodo:
        @etapas.etapa(clave)
        async def nodo(estado: EstadoAnalisis) -> dict[str, Any]:
            self.ejecutadas.append(clave)
            if clave in self.al_ejecutar:
                await self.al_ejecutar[clave](estado)
            if clave in self.fallos:
                raise self.fallos.pop(clave)  # falla una sola vez
            if clave == "justificacion":
                # Las etapas de prueba producen un resultado sintético para el cierre real.
                async with get_sessionmaker()() as session:
                    licitacion = await session.get(Licitacion, uuid.UUID(estado["licitacion_id"]))
                    trabajo = await session.get(
                        TrabajoProcesamiento, uuid.UUID(estado["trabajo_id"])
                    )
                    assert licitacion is not None and trabajo is not None
                    licitacion.recomendacion = "condicionada"
                    licitacion.justificacion = "Justificación sintética."
                    trabajo.detalle = {**(trabajo.detalle or {}), "justificacion_publicada": True}
                    await session.commit()
            if clave == "cierre":
                return await etapas.cerrar.__wrapped__(estado)  # type: ignore[attr-defined,no-any-return]
            return {}

        return nodo

    def grafo(self, con_checkpoint: bool = True) -> Any:
        nodos = [(e.clave, self._nodo(e.clave)) for e in catalogo.ETAPAS]
        return grafo.construir(InMemorySaver() if con_checkpoint else None, nodos)


@pytest.fixture
async def revisor(sesion: AsyncSession) -> Identidad:
    return await crear_organizacion(sesion)


async def _trabajo_en_cola(sesion: AsyncSession, quien: Identidad) -> TrabajoProcesamiento:
    licitacion = Licitacion(
        organizacion_id=quien.organizacion_id,
        creado_por=quien.usuario_id,
        titulo="Pliego",
        nombre_archivo="pliego.pdf",
        storage_key=f"organizaciones/{quien.organizacion_id}/pliegos/{uuid.uuid4()}.pdf",
    )
    sesion.add(licitacion)
    await sesion.flush()
    trabajo = TrabajoProcesamiento(
        organizacion_id=quien.organizacion_id,
        tipo=TipoTrabajo.ANALISIS,
        licitacion_id=licitacion.id,
    )
    sesion.add(trabajo)
    await sesion.commit()
    return trabajo


async def _procesar(trabajo: TrabajoProcesamiento, grafo_analisis: Any) -> MensajeFalso:
    mensaje = mensaje_de(trabajo.id)
    await handlers.procesar(mensaje, TipoTrabajo.ANALISIS, grafo_analisis)  # type: ignore[arg-type]
    return mensaje


async def _recargar(sesion: AsyncSession, trabajo: TrabajoProcesamiento) -> Licitacion:
    await sesion.refresh(trabajo)
    licitacion = await sesion.get(Licitacion, trabajo.licitacion_id)
    assert licitacion is not None
    await sesion.refresh(licitacion)
    return licitacion


# --- Criterio 1: un mensaje a la vez, etapas en el orden del DAS ----------------------------


def test_el_grafo_recorre_las_etapas_en_el_orden_del_das() -> None:
    assert [clave for clave, _ in etapas.ETAPAS] == ORDEN_DAS
    assert [e.clave for e in catalogo.ETAPAS] == ORDEN_DAS


async def test_las_etapas_se_ejecutan_en_orden(sesion: AsyncSession, revisor: Identidad) -> None:
    trabajo = await _trabajo_en_cola(sesion, revisor)
    prueba = EtapasDePrueba()

    await _procesar(trabajo, prueba.grafo())

    assert prueba.ejecutadas == ORDEN_DAS


class _ColaFalsa:
    def __init__(self, nombre: str) -> None:
        self.name = nombre
        self.consumidores: list[Any] = []

    async def consume(self, consumidor: Any) -> None:
        self.consumidores.append(consumidor)


class _CanalFalso:
    def __init__(self) -> None:
        self.prefetch: int | None = None

    async def set_qos(self, prefetch_count: int, global_: bool = False) -> None:
        self.global_ = global_
        self.prefetch = prefetch_count


async def test_el_worker_toma_un_solo_mensaje_a_la_vez(monkeypatch: pytest.MonkeyPatch) -> None:
    colas = {tipo: _ColaFalsa(topologia.nombre_cola(tipo)) for tipo in TipoTrabajo}

    async def declarar(_canal: Any) -> tuple[None, dict[TipoTrabajo, _ColaFalsa]]:
        return None, colas

    monkeypatch.setattr(topologia, "declarar", declarar)
    canal = _CanalFalso()

    await worker.suscribir(canal, grafo_analisis=object())  # type: ignore[arg-type]

    assert canal.prefetch == 1 and canal.global_
    assert all(len(cola.consumidores) == 1 for cola in colas.values())


# --- Criterio 2: cada etapa actualiza la etapa y el progreso en PostgreSQL -------------------


async def test_cada_etapa_registra_su_avance_en_postgresql(
    sesion: AsyncSession, revisor: Identidad
) -> None:
    trabajo = await _trabajo_en_cola(sesion, revisor)
    vistos: list[tuple[str | None, int, EstadoTrabajo]] = []

    async def leer_avance(_estado: EstadoAnalisis) -> None:
        await sesion.refresh(trabajo)
        vistos.append((trabajo.etapa_actual, trabajo.progreso, trabajo.estado))

    prueba = EtapasDePrueba(al_ejecutar=dict.fromkeys(ORDEN_DAS, leer_avance))
    await _procesar(trabajo, prueba.grafo())

    assert vistos == [(e.clave, e.porcentaje, EstadoTrabajo.EN_EJECUCION) for e in catalogo.ETAPAS]


# --- Criterio 3: el mensaje se confirma solo cuando el análisis termina ----------------------


async def test_el_mensaje_se_confirma_solo_al_terminar(
    sesion: AsyncSession, revisor: Identidad
) -> None:
    trabajo = await _trabajo_en_cola(sesion, revisor)
    mensaje = mensaje_de(trabajo.id)
    confirmado_durante: list[bool] = []

    async def mirar(_estado: EstadoAnalisis) -> None:
        confirmado_durante.append(mensaje.confirmado)

    prueba = EtapasDePrueba(al_ejecutar=dict.fromkeys(ORDEN_DAS, mirar))
    await handlers.procesar(mensaje, TipoTrabajo.ANALISIS, prueba.grafo())  # type: ignore[arg-type]

    assert confirmado_durante == [False] * len(ORDEN_DAS)
    assert mensaje.confirmado and not mensaje.rechazado
    licitacion = await _recargar(sesion, trabajo)
    assert trabajo.estado == EstadoTrabajo.COMPLETADO
    assert trabajo.progreso == 100 and trabajo.finalizado_en is not None
    assert licitacion.estado == EstadoLicitacion.COMPLETADA


# --- Criterio 4: una etapa que falla deja el trabajo en error, sin reintento automático ------


async def test_si_una_etapa_falla_el_trabajo_queda_en_error_con_su_etapa(
    sesion: AsyncSession, revisor: Identidad
) -> None:
    trabajo = await _trabajo_en_cola(sesion, revisor)
    prueba = EtapasDePrueba(
        fallos={"extraccion": RuntimeError("clave sk-ant-123 en /app/src/interno.py")}
    )

    mensaje = await _procesar(trabajo, prueba.grafo())

    licitacion = await _recargar(sesion, trabajo)
    assert trabajo.estado == EstadoTrabajo.FALLIDO
    assert trabajo.etapa_actual == "extraccion"
    assert trabajo.error == (
        "El análisis falló en la etapa «Extracción de requisitos». Puede reintentarlo."
    )
    assert licitacion.estado == EstadoLicitacion.FALLIDA
    # Sin reencolar: va a la cola de fallidos y no se reintenta solo.
    assert mensaje.rechazado and mensaje.reencolado is False and not mensaje.confirmado
    assert prueba.ejecutadas == ORDEN_DAS[: ORDEN_DAS.index("extraccion") + 1]


async def test_un_error_previsto_muestra_su_propio_mensaje(
    sesion: AsyncSession, revisor: Identidad
) -> None:
    trabajo = await _trabajo_en_cola(sesion, revisor)
    limite = "El pliego tiene 900 páginas y el máximo es 300."
    prueba = EtapasDePrueba(fallos={"lectura_pdf": AnalisisError(limite)})

    await _procesar(trabajo, prueba.grafo())

    await _recargar(sesion, trabajo)
    assert trabajo.estado == EstadoTrabajo.FALLIDO and trabajo.error == limite


async def test_un_mensaje_que_no_se_puede_leer_va_a_fallidos() -> None:
    mensaje = MensajeFalso(b"esto no es un mensaje de trabajo")

    await handlers.procesar(mensaje, TipoTrabajo.ANALISIS, object())  # type: ignore[arg-type]

    assert mensaje.rechazado and mensaje.reencolado is False


async def test_un_trabajo_que_tumba_al_worker_no_se_reintenta_indefinidamente(
    sesion: AsyncSession, revisor: Identidad
) -> None:
    """Sin confirmación RabbitMQ reentrega el mensaje; tras el máximo se abandona."""
    trabajo = await _trabajo_en_cola(sesion, revisor)
    trabajo.estado = EstadoTrabajo.EN_EJECUCION
    trabajo.intentos = get_settings().pipeline_max_intentos
    await sesion.commit()
    prueba = EtapasDePrueba()

    mensaje = await _procesar(trabajo, prueba.grafo())

    licitacion = await _recargar(sesion, trabajo)
    assert mensaje.rechazado and mensaje.reencolado is False
    assert trabajo.estado == EstadoTrabajo.FALLIDO
    assert trabajo.error == handlers.MENSAJE_DEMASIADOS_INTENTOS
    assert licitacion.estado == EstadoLicitacion.FALLIDA
    assert prueba.ejecutadas == []


async def test_un_trabajo_que_supera_el_tiempo_maximo_falla(
    sesion: AsyncSession, revisor: Identidad, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PIPELINE_TIMEOUT_SEGUNDOS", "1")
    get_settings.cache_clear()
    trabajo = await _trabajo_en_cola(sesion, revisor)

    async def colgarse(_estado: EstadoAnalisis) -> None:
        await asyncio.sleep(30)

    prueba = EtapasDePrueba(al_ejecutar={"filtrado": colgarse})
    mensaje = await _procesar(trabajo, prueba.grafo())

    await _recargar(sesion, trabajo)
    assert trabajo.estado == EstadoTrabajo.FALLIDO
    assert trabajo.error == handlers.MENSAJE_TIEMPO_AGOTADO
    assert mensaje.rechazado and not mensaje.confirmado


# --- Criterio 5: el reintento continúa desde la última etapa completada ----------------------


async def test_el_reintento_no_repite_las_etapas_ya_completadas(
    cliente: httpx.AsyncClient, sesion: AsyncSession, revisor: Identidad
) -> None:
    trabajo = await _trabajo_en_cola(sesion, revisor)
    prueba = EtapasDePrueba(fallos={"verificacion": ConnectionError("proveedor caído")})
    grafo_analisis = prueba.grafo()

    await _procesar(trabajo, grafo_analisis)
    await _recargar(sesion, trabajo)
    assert trabajo.estado == EstadoTrabajo.FALLIDO and trabajo.etapa_actual == "verificacion"

    reintento = await cliente.post(
        f"/api/licitaciones/{trabajo.licitacion_id}/reintentar", headers=revisor.cabeceras
    )
    assert reintento.status_code == 202
    mensaje = await _procesar(trabajo, grafo_analisis)

    licitacion = await _recargar(sesion, trabajo)
    assert mensaje.confirmado
    assert trabajo.estado == EstadoTrabajo.COMPLETADO and trabajo.error is None
    assert licitacion.estado == EstadoLicitacion.COMPLETADA
    # Las etapas anteriores al fallo (con llamadas ya pagadas) se ejecutaron una sola vez.
    previas = ORDEN_DAS[: ORDEN_DAS.index("verificacion")]
    assert all(prueba.ejecutadas.count(clave) == 1 for clave in previas)
    assert prueba.ejecutadas.count("verificacion") == 2
    assert prueba.ejecutadas[-5:] == ORDEN_DAS[-5:]


async def test_reintentar_reinicia_el_contador_de_entregas(
    cliente: httpx.AsyncClient, sesion: AsyncSession, revisor: Identidad
) -> None:
    trabajo = await _trabajo_en_cola(sesion, revisor)
    trabajo.estado = EstadoTrabajo.FALLIDO
    trabajo.intentos = get_settings().pipeline_max_intentos
    await sesion.commit()

    respuesta = await cliente.post(
        f"/api/licitaciones/{trabajo.licitacion_id}/reintentar", headers=revisor.cabeceras
    )

    assert respuesta.status_code == 202
    await sesion.refresh(trabajo)
    assert trabajo.intentos == 0 and trabajo.estado == EstadoTrabajo.EN_COLA


async def test_el_checkpoint_en_postgresql_permite_reanudar(
    sesion: AsyncSession, revisor: Identidad
) -> None:
    """Mismo flujo con el checkpointer real del worker (P-08), no el de memoria."""
    trabajo = await _trabajo_en_cola(sesion, revisor)
    prueba = EtapasDePrueba(fallos={"decision": RuntimeError("fallo transitorio")})
    nodos = [(e.clave, prueba._nodo(e.clave)) for e in catalogo.ETAPAS]

    async with worker._checkpointer() as checkpointer:
        assert isinstance(checkpointer, AsyncPostgresSaver)
        grafo_analisis = grafo.construir(checkpointer, nodos)
        primero = await _procesar(trabajo, grafo_analisis)
        assert primero.rechazado

    # Otro proceso del worker, con una conexión nueva: el estado sale de PostgreSQL.
    async with worker._checkpointer() as checkpointer:
        grafo_analisis = grafo.construir(checkpointer, nodos)
        trabajo.estado, trabajo.intentos = EstadoTrabajo.EN_COLA, 0
        await sesion.commit()
        segundo = await _procesar(trabajo, grafo_analisis)

    await _recargar(sesion, trabajo)
    assert segundo.confirmado and trabajo.estado == EstadoTrabajo.COMPLETADO
    assert prueba.ejecutadas == [*ORDEN_DAS[: ORDEN_DAS.index("decision") + 1], *ORDEN_DAS[-3:]]


# --- Criterio 6: una entrega duplicada no duplica resultados ---------------------------------


async def test_la_entrega_duplicada_de_un_trabajo_terminado_no_repite_nada(
    sesion: AsyncSession, revisor: Identidad
) -> None:
    trabajo = await _trabajo_en_cola(sesion, revisor)
    prueba = EtapasDePrueba()
    grafo_analisis = prueba.grafo()

    await _procesar(trabajo, grafo_analisis)
    duplicado = await _procesar(trabajo, grafo_analisis)

    assert duplicado.confirmado
    assert prueba.ejecutadas == ORDEN_DAS  # una sola pasada


async def test_un_grafo_ya_terminado_no_se_vuelve_a_ejecutar(
    sesion: AsyncSession, revisor: Identidad
) -> None:
    trabajo = await _trabajo_en_cola(sesion, revisor)
    prueba = EtapasDePrueba()
    grafo_analisis = prueba.grafo()
    estado = EstadoAnalisis(
        trabajo_id=str(trabajo.id),
        licitacion_id=str(trabajo.licitacion_id),
        organizacion_id=str(trabajo.organizacion_id),
    )

    await grafo.ejecutar(grafo_analisis, estado)
    await grafo.ejecutar(grafo_analisis, estado)

    assert prueba.ejecutadas == ORDEN_DAS


async def test_reentrega_despues_del_cierre_conserva_la_licitacion_completada(
    sesion: AsyncSession, revisor: Identidad
) -> None:
    trabajo = await _trabajo_en_cola(sesion, revisor)
    prueba = EtapasDePrueba()
    grafo_analisis = prueba.grafo()
    estado = EstadoAnalisis(
        trabajo_id=str(trabajo.id),
        licitacion_id=str(trabajo.licitacion_id),
        organizacion_id=str(trabajo.organizacion_id),
    )
    trabajo.estado = EstadoTrabajo.EN_EJECUCION
    await sesion.commit()
    await grafo.ejecutar(grafo_analisis, estado)

    reentrega = await _procesar(trabajo, grafo_analisis)

    licitacion = await _recargar(sesion, trabajo)
    assert reentrega.confirmado and trabajo.estado == EstadoTrabajo.COMPLETADO
    assert licitacion.estado == EstadoLicitacion.COMPLETADA
    assert prueba.ejecutadas == ORDEN_DAS


async def test_la_reentrega_tras_una_caida_reanuda_donde_quedo(
    sesion: AsyncSession, revisor: Identidad
) -> None:
    """El worker se detiene a mitad (sin confirmar ni rechazar) y RabbitMQ reentrega."""
    trabajo = await _trabajo_en_cola(sesion, revisor)
    en_localizacion = asyncio.Event()
    visitas = 0

    async def quedarse_colgado(_estado: EstadoAnalisis) -> None:
        nonlocal visitas
        visitas += 1
        if visitas == 1:
            en_localizacion.set()
            await asyncio.Event().wait()  # hasta que el worker sea detenido

    prueba = EtapasDePrueba(al_ejecutar={"localizacion": quedarse_colgado})
    grafo_analisis = prueba.grafo()

    interrumpido = mensaje_de(trabajo.id)
    tarea = asyncio.create_task(
        handlers.procesar(interrumpido, TipoTrabajo.ANALISIS, grafo_analisis)  # type: ignore[arg-type]
    )
    await en_localizacion.wait()
    tarea.cancel()
    with pytest.raises(asyncio.CancelledError):
        await tarea
    assert not interrumpido.confirmado and not interrumpido.rechazado
    await _recargar(sesion, trabajo)
    assert trabajo.estado == EstadoTrabajo.EN_EJECUCION
    assert trabajo.etapa_actual == "localizacion"

    reentregado = await _procesar(trabajo, grafo_analisis)

    await _recargar(sesion, trabajo)
    assert reentregado.confirmado
    assert trabajo.estado == EstadoTrabajo.COMPLETADO and trabajo.intentos == 2
    previas = ORDEN_DAS[: ORDEN_DAS.index("localizacion")]
    assert all(prueba.ejecutadas.count(clave) == 1 for clave in previas)
    assert prueba.ejecutadas.count("localizacion") == 2


# --- Calidad: un fallo no bloquea al worker para los siguientes trabajos ---------------------


async def test_un_fallo_de_proveedor_no_bloquea_el_siguiente_trabajo(
    sesion: AsyncSession, revisor: Identidad
) -> None:
    primero = await _trabajo_en_cola(sesion, revisor)
    segundo = await _trabajo_en_cola(sesion, revisor)
    prueba = EtapasDePrueba(fallos={"filtrado": ConnectionError("NVIDIA no responde")})
    grafo_analisis = prueba.grafo()

    await _procesar(primero, grafo_analisis)
    mensaje = await _procesar(segundo, grafo_analisis)

    await _recargar(sesion, primero)
    await _recargar(sesion, segundo)
    assert primero.estado == EstadoTrabajo.FALLIDO
    assert segundo.estado == EstadoTrabajo.COMPLETADO and mensaje.confirmado
