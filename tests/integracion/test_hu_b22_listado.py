"""HU-B22: listar las licitaciones de la organización (RF-11)."""

from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from analizador.models import Licitacion
from analizador.models.enums import EstadoLicitacion, Rol
from tests.apoyo import Identidad, agregar_miembro, crear_organizacion

AHORA = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)


async def _licitacion(
    sesion: AsyncSession,
    quien: Identidad,
    titulo: str,
    horas: int,
    estado: EstadoLicitacion = EstadoLicitacion.COMPLETADA,
    recomendacion: str | None = "presentarse_con_riesgo",
) -> Licitacion:
    licitacion = Licitacion(
        organizacion_id=quien.organizacion_id,
        creado_por=quien.usuario_id,
        titulo=titulo,
        nombre_archivo=f"{titulo}.pdf",
        storage_key=f"organizaciones/{quien.organizacion_id}/pliegos/{titulo}.pdf",
        estado=estado,
        recomendacion=recomendacion,
        creado_en=AHORA - timedelta(hours=horas),
    )
    sesion.add(licitacion)
    await sesion.commit()
    return licitacion


# --- Criterio 1: nombre, estado, recomendación y fecha de carga ----------------------------


async def test_el_listado_trae_nombre_estado_recomendacion_y_fecha(
    cliente: httpx.AsyncClient, sesion: AsyncSession
) -> None:
    admin = await crear_organizacion(sesion)
    await _licitacion(sesion, admin, "Terminada", 2)
    await _licitacion(sesion, admin, "En curso", 1, EstadoLicitacion.PROCESANDO, recomendacion=None)

    respuesta = await cliente.get("/api/licitaciones", headers=admin.cabeceras)

    assert respuesta.status_code == 200
    por_titulo = {item["titulo"]: item for item in respuesta.json()}
    assert por_titulo["Terminada"]["estado"] == "completada"
    assert por_titulo["Terminada"]["recomendacion"] == "presentarse_con_riesgo"
    assert por_titulo["En curso"]["estado"] == "procesando"
    assert por_titulo["En curso"]["recomendacion"] is None
    assert datetime.fromisoformat(por_titulo["Terminada"]["creado_en"]) == AHORA - timedelta(
        hours=2
    )


# --- Criterio 2: solo las de la organización del usuario -----------------------------------


async def test_solo_incluye_las_licitaciones_de_la_organizacion_y_las_ve_un_revisor(
    cliente: httpx.AsyncClient, sesion: AsyncSession
) -> None:
    admin_a = await crear_organizacion(sesion, "A")
    admin_b = await crear_organizacion(sesion, "B")
    revisor_a = await agregar_miembro(sesion, admin_a.organizacion_id, Rol.REVISOR)
    await _licitacion(sesion, admin_a, "Propia", 1)
    await _licitacion(sesion, admin_b, "Ajena", 1)

    for quien in (admin_a, revisor_a):
        respuesta = await cliente.get("/api/licitaciones", headers=quien.cabeceras)
        assert [item["titulo"] for item in respuesta.json()] == ["Propia"]


# --- Criterio 3: orden por fecha de carga --------------------------------------------------


async def test_admite_orden_por_fecha_de_carga(
    cliente: httpx.AsyncClient, sesion: AsyncSession
) -> None:
    admin = await crear_organizacion(sesion)
    for titulo, horas in (("Media", 2), ("Antigua", 3), ("Reciente", 1)):
        await _licitacion(sesion, admin, titulo, horas)

    por_defecto = await cliente.get("/api/licitaciones", headers=admin.cabeceras)
    recientes = await cliente.get(
        "/api/licitaciones", headers=admin.cabeceras, params={"orden": "recientes"}
    )
    antiguas = await cliente.get(
        "/api/licitaciones", headers=admin.cabeceras, params={"orden": "antiguas"}
    )
    invalido = await cliente.get(
        "/api/licitaciones", headers=admin.cabeceras, params={"orden": "titulo"}
    )

    assert [i["titulo"] for i in por_defecto.json()] == ["Reciente", "Media", "Antigua"]
    assert [i["titulo"] for i in recientes.json()] == ["Reciente", "Media", "Antigua"]
    assert [i["titulo"] for i in antiguas.json()] == ["Antigua", "Media", "Reciente"]
    assert invalido.status_code == 422
