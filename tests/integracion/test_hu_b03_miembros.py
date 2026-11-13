"""HU-B03: gestionar miembros y roles (RF-09, S-05)."""

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from analizador.models import AuditoriaMiembro, Miembro
from analizador.models.enums import AccionMiembro, Rol
from tests.apoyo import agregar_miembro, crear_organizacion

# --- Criterio 1: lista con correo, rol e invitación pendiente --------------------------------


async def test_la_lista_muestra_correo_rol_e_invitacion_pendiente(
    cliente: httpx.AsyncClient, sesion: AsyncSession
) -> None:
    admin = await crear_organizacion(sesion)
    invitado = await agregar_miembro(sesion, admin.organizacion_id, Rol.REVISOR)
    miembro = await sesion.get(Miembro, invitado.miembro_id)
    assert miembro is not None
    miembro.invitacion_pendiente = True
    await sesion.commit()

    lista = await cliente.get("/api/miembros", headers=admin.cabeceras)
    assert lista.status_code == 200
    por_id = {m["id"]: m for m in lista.json()}
    assert por_id[str(invitado.miembro_id)]["rol"] == "revisor"
    assert por_id[str(invitado.miembro_id)]["email"] == miembro.email
    assert por_id[str(invitado.miembro_id)]["invitacion_pendiente"] is True
    assert por_id[str(admin.miembro_id)]["invitacion_pendiente"] is False

    # La primera petición autenticada del invitado confirma que aceptó la invitación.
    assert (await cliente.get("/api/licitaciones", headers=invitado.cabeceras)).status_code == 200
    lista = await cliente.get("/api/miembros", headers=admin.cabeceras)
    pendientes = {m["id"]: m["invitacion_pendiente"] for m in lista.json()}
    assert pendientes[str(invitado.miembro_id)] is False


# --- Criterio 2: cambiar el rol o retirar a un miembro -------------------------------------


async def test_el_administrador_cambia_el_rol_y_retira_miembros(
    cliente: httpx.AsyncClient, sesion: AsyncSession
) -> None:
    admin = await crear_organizacion(sesion)
    revisor = await agregar_miembro(sesion, admin.organizacion_id, Rol.REVISOR)

    ascenso = await cliente.patch(
        f"/api/miembros/{revisor.miembro_id}/rol",
        headers=admin.cabeceras,
        json={"rol": "administrador"},
    )
    assert ascenso.status_code == 200
    assert ascenso.json()["rol"] == "administrador"

    retiro = await cliente.delete(f"/api/miembros/{revisor.miembro_id}", headers=admin.cabeceras)
    assert retiro.status_code == 204
    sesion.expire_all()
    assert await sesion.get(Miembro, revisor.miembro_id) is None


# --- Criterio 3: nunca sin administrador ---------------------------------------------------


@pytest.mark.parametrize("operacion", ["degradar", "retirar"])
async def test_no_se_retira_ni_degrada_al_ultimo_administrador(
    cliente: httpx.AsyncClient, sesion: AsyncSession, operacion: str
) -> None:
    admin = await crear_organizacion(sesion)
    await agregar_miembro(sesion, admin.organizacion_id, Rol.REVISOR)
    url = f"/api/miembros/{admin.miembro_id}"

    if operacion == "degradar":
        respuesta = await cliente.patch(
            f"{url}/rol", headers=admin.cabeceras, json={"rol": "revisor"}
        )
    else:
        respuesta = await cliente.delete(url, headers=admin.cabeceras)

    assert respuesta.status_code == 409
    assert "último administrador" in respuesta.json()["detail"]
    sesion.expire_all()
    miembro = await sesion.get(Miembro, admin.miembro_id)
    assert miembro is not None and miembro.rol == Rol.ADMINISTRADOR


async def test_con_otro_administrador_si_se_puede_degradar(
    cliente: httpx.AsyncClient, sesion: AsyncSession
) -> None:
    admin = await crear_organizacion(sesion)
    otro = await agregar_miembro(sesion, admin.organizacion_id, Rol.ADMINISTRADOR)

    respuesta = await cliente.patch(
        f"/api/miembros/{admin.miembro_id}/rol", headers=otro.cabeceras, json={"rol": "revisor"}
    )
    assert respuesta.status_code == 200

    # Ahora `otro` es el único administrador: no puede degradarse.
    propio = await cliente.patch(
        f"/api/miembros/{otro.miembro_id}/rol", headers=otro.cabeceras, json={"rol": "revisor"}
    )
    assert propio.status_code == 409


# --- Criterio 4: un revisor no cambia roles, ni siquiera el suyo ----------------------------


async def test_un_revisor_no_cambia_roles_ni_el_suyo(
    cliente: httpx.AsyncClient, sesion: AsyncSession
) -> None:
    admin = await crear_organizacion(sesion)
    revisor = await agregar_miembro(sesion, admin.organizacion_id, Rol.REVISOR)

    for miembro_id in (revisor.miembro_id, admin.miembro_id):
        respuesta = await cliente.patch(
            f"/api/miembros/{miembro_id}/rol",
            headers=revisor.cabeceras,
            json={"rol": "administrador"},
        )
        assert respuesta.status_code == 403
    retiro = await cliente.delete(f"/api/miembros/{admin.miembro_id}", headers=revisor.cabeceras)
    assert retiro.status_code == 403

    sesion.expire_all()
    miembro = await sesion.get(Miembro, revisor.miembro_id)
    assert miembro is not None and miembro.rol == Rol.REVISOR


# --- Criterio 5: un miembro retirado pierde el acceso en su siguiente petición --------------


async def test_un_miembro_retirado_pierde_el_acceso_en_la_siguiente_peticion(
    cliente: httpx.AsyncClient, sesion: AsyncSession
) -> None:
    admin = await crear_organizacion(sesion)
    revisor = await agregar_miembro(sesion, admin.organizacion_id, Rol.REVISOR)
    assert (await cliente.get("/api/licitaciones", headers=revisor.cabeceras)).status_code == 200

    await cliente.delete(f"/api/miembros/{revisor.miembro_id}", headers=admin.cabeceras)

    assert (await cliente.get("/api/licitaciones", headers=revisor.cabeceras)).status_code == 403


# --- Calidad: cada cambio de rol o retiro queda registrado con autor y fecha ----------------


async def test_cambios_de_rol_y_retiros_quedan_auditados(
    cliente: httpx.AsyncClient, sesion: AsyncSession
) -> None:
    admin = await crear_organizacion(sesion)
    revisor = await agregar_miembro(sesion, admin.organizacion_id, Rol.REVISOR)
    url = f"/api/miembros/{revisor.miembro_id}"

    await cliente.patch(f"{url}/rol", headers=admin.cabeceras, json={"rol": "administrador"})
    await cliente.patch(f"{url}/rol", headers=admin.cabeceras, json={"rol": "administrador"})
    await cliente.delete(url, headers=admin.cabeceras)

    registros = list(
        await sesion.scalars(select(AuditoriaMiembro).order_by(AuditoriaMiembro.creado_en))
    )
    assert [r.accion for r in registros] == [AccionMiembro.CAMBIO_ROL, AccionMiembro.RETIRO]
    cambio, retiro = registros
    assert (cambio.rol_anterior, cambio.rol_nuevo) == (Rol.REVISOR, Rol.ADMINISTRADOR)
    assert retiro.rol_anterior == Rol.ADMINISTRADOR
    for registro in registros:
        assert registro.actor_usuario_id == admin.usuario_id
        assert registro.miembro_usuario_id == revisor.usuario_id
        assert registro.creado_en is not None
