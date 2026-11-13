"""HU-B01: verificar identidad y resolver organización y rol (RF-09, ADR-008, ADR-014, S-04)."""

import re
import time
import uuid
from typing import Any

import httpx
import jwt
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from analizador.models.enums import Rol
from tests.apoyo import EMISOR, OTRA_CLAVE, agregar_miembro, cabeceras, crear_organizacion, token

RUTAS_PUBLICAS = {"/health", "/health/ready"}


# --- Criterio 1: organización y rol salen de PostgreSQL -------------------------------------


async def test_token_valido_resuelve_organizacion_y_rol_desde_la_base(
    cliente: httpx.AsyncClient, sesion: AsyncSession
) -> None:
    admin = await crear_organizacion(sesion)

    yo = await cliente.get("/api/yo", headers=admin.cabeceras)
    assert yo.status_code == 200
    assert yo.json()["usuario_id"] == str(admin.usuario_id)
    [membresia] = yo.json()["membresias"]
    assert membresia["organizacion_id"] == str(admin.organizacion_id)
    assert membresia["rol"] == "administrador"

    miembros = await cliente.get("/api/miembros", headers=admin.cabeceras)
    assert miembros.status_code == 200
    assert [m["id"] for m in miembros.json()] == [str(admin.miembro_id)]


async def test_usuario_con_dos_organizaciones_elige_solo_entre_las_suyas(
    cliente: httpx.AsyncClient, sesion: AsyncSession
) -> None:
    en_a = await crear_organizacion(sesion, "A", Rol.ADMINISTRADOR)
    org_b = await crear_organizacion(sesion, "B")
    en_b = await agregar_miembro(sesion, org_b.organizacion_id, Rol.REVISOR, en_a.usuario_id)

    sin_elegir = await cliente.get("/api/licitaciones", headers=en_a.cabeceras)
    assert sin_elegir.status_code == 400

    como_admin_de_a = await cliente.get(
        "/api/miembros",
        headers={**en_a.cabeceras, "X-Organizacion-Id": str(en_a.organizacion_id)},
    )
    assert como_admin_de_a.status_code == 200

    # En B es revisor: el rol depende de la membresía elegida, no del usuario.
    como_revisor_de_b = await cliente.get(
        "/api/miembros",
        headers={**en_a.cabeceras, "X-Organizacion-Id": str(en_b.organizacion_id)},
    )
    assert como_revisor_de_b.status_code == 403


# --- Criterio 2: token ausente, mal firmado, expirado o con audiencia incorrecta → 401 ------


def _hs256() -> str:
    return jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "aud": "authenticated",
            "iss": EMISOR,
            "exp": int(time.time()) + 60,
        },
        "secreto-compartido-de-al-menos-32-bytes!!",
        algorithm="HS256",
    )


@pytest.mark.parametrize(
    "autorizacion",
    [
        pytest.param(None, id="ausente"),
        pytest.param("Bearer no-es-un-jwt", id="malformado"),
        pytest.param(f"Bearer {token(clave=OTRA_CLAVE)}", id="firmado-con-otra-clave"),
        pytest.param(f"Bearer {token(exp=int(time.time()) - 10)}", id="expirado"),
        pytest.param(f"Bearer {token(aud='otra-audiencia')}", id="audiencia-incorrecta"),
        pytest.param(f"Bearer {token(iss='https://otro.supabase.co/auth/v1')}", id="otro-emisor"),
        pytest.param(f"Bearer {_hs256()}", id="algoritmo-simetrico"),
        pytest.param(f"Basic {token()}", id="esquema-no-bearer"),
    ],
)
async def test_token_invalido_es_401(cliente: httpx.AsyncClient, autorizacion: str | None) -> None:
    cabeceras_peticion = {"Authorization": autorizacion} if autorizacion else {}
    respuesta = await cliente.get("/api/licitaciones", headers=cabeceras_peticion)
    assert respuesta.status_code == 401
    assert respuesta.headers["www-authenticate"] == "Bearer"


async def test_todo_endpoint_salvo_salud_exige_token(
    app: FastAPI, cliente: httpx.AsyncClient
) -> None:
    """Se recorre el contrato OpenAPI para no olvidar ningún endpoint nuevo."""
    protegidas = {
        ruta: operaciones
        for ruta, operaciones in app.openapi()["paths"].items()
        if ruta not in RUTAS_PUBLICAS
    }
    assert protegidas, "la aplicación debería exponer rutas protegidas"
    for ruta, operaciones in protegidas.items():
        url = re.sub(r"\{[^}]+\}", str(uuid.uuid4()), ruta)
        for metodo in operaciones:
            respuesta = await cliente.request(metodo.upper(), url)
            assert respuesta.status_code == 401, f"{metodo.upper()} {ruta} no exige token"


# --- Criterio 3: autenticado sin registro de miembro → 403 ----------------------------------


async def test_usuario_sin_membresia_es_403(
    cliente: httpx.AsyncClient, sesion: AsyncSession
) -> None:
    await crear_organizacion(sesion)
    respuesta = await cliente.get("/api/licitaciones", headers=cabeceras(uuid.uuid4()))
    assert respuesta.status_code == 403


# --- Criterio 4: organización y rol enviados por el cliente se ignoran ----------------------


@pytest.mark.parametrize(
    "extra",
    [
        pytest.param({"headers": {"X-Rol": "administrador", "X-Role": "admin"}}, id="cabeceras"),
        pytest.param({"params": {"rol": "administrador"}}, id="parametros"),
    ],
)
async def test_rol_enviado_por_el_cliente_se_ignora(
    cliente: httpx.AsyncClient, sesion: AsyncSession, extra: dict[str, Any]
) -> None:
    admin = await crear_organizacion(sesion)
    revisor = await agregar_miembro(sesion, admin.organizacion_id, Rol.REVISOR)
    peticion = {"headers": {**revisor.cabeceras, **extra.get("headers", {})}}
    if "params" in extra:
        peticion["params"] = extra["params"]
    respuesta = await cliente.get("/api/miembros", **peticion)
    assert respuesta.status_code == 403


async def test_rol_y_organizacion_en_el_token_se_ignoran(
    cliente: httpx.AsyncClient, sesion: AsyncSession
) -> None:
    """Los metadatos de usuario de Supabase los puede editar el propio usuario."""
    admin = await crear_organizacion(sesion)
    revisor = await agregar_miembro(sesion, admin.organizacion_id, Rol.REVISOR)
    falsificado = cabeceras(
        revisor.usuario_id,
        role="administrador",
        user_metadata={"rol": "administrador", "organizacion_id": str(uuid.uuid4())},
        app_metadata={"rol": "administrador"},
    )
    assert (await cliente.get("/api/miembros", headers=falsificado)).status_code == 403
    yo = await cliente.get("/api/yo", headers=falsificado)
    assert [m["rol"] for m in yo.json()["membresias"]] == ["revisor"]


async def test_organizacion_en_el_cuerpo_o_la_url_se_ignora(
    cliente: httpx.AsyncClient, sesion: AsyncSession
) -> None:
    admin_a = await crear_organizacion(sesion, "A")
    admin_b = await crear_organizacion(sesion, "B")

    respuesta = await cliente.get(
        "/api/miembros",
        headers=admin_a.cabeceras,
        params={"organizacion_id": str(admin_b.organizacion_id)},
    )
    assert [m["organizacion_id"] for m in respuesta.json()] == [str(admin_a.organizacion_id)]

    cambio = await cliente.patch(
        f"/api/miembros/{admin_a.miembro_id}/rol",
        headers=admin_a.cabeceras,
        json={"rol": "administrador", "organizacion_id": str(admin_b.organizacion_id)},
    )
    assert cambio.status_code == 200
    assert cambio.json()["organizacion_id"] == str(admin_a.organizacion_id)


async def test_cabecera_de_organizacion_ajena_no_concede_acceso_ni_revela_si_existe(
    cliente: httpx.AsyncClient, sesion: AsyncSession
) -> None:
    propia = await crear_organizacion(sesion, "A")
    ajena = await crear_organizacion(sesion, "B")

    existente = await cliente.get(
        "/api/licitaciones",
        headers={**propia.cabeceras, "X-Organizacion-Id": str(ajena.organizacion_id)},
    )
    inexistente = await cliente.get(
        "/api/licitaciones",
        headers={**propia.cabeceras, "X-Organizacion-Id": str(uuid.uuid4())},
    )
    assert existente.status_code == inexistente.status_code == 403
    assert existente.json() == inexistente.json()


# --- Criterio 5: un revisor no entra a endpoints de administrador ---------------------------


async def test_revisor_en_endpoints_de_administrador_es_403(
    cliente: httpx.AsyncClient, sesion: AsyncSession
) -> None:
    admin = await crear_organizacion(sesion)
    revisor = await agregar_miembro(sesion, admin.organizacion_id, Rol.REVISOR)

    intentos = [
        ("GET", "/api/miembros", None),
        ("POST", "/api/miembros/invitaciones", {"email": "nuevo@ejemplo.co"}),
        ("PATCH", f"/api/miembros/{revisor.miembro_id}/rol", {"rol": "administrador"}),
        ("DELETE", f"/api/miembros/{admin.miembro_id}", None),
        ("POST", "/api/conocimiento/indexacion", None),
    ]
    for metodo, url, cuerpo in intentos:
        respuesta = await cliente.request(metodo, url, headers=revisor.cabeceras, json=cuerpo)
        assert respuesta.status_code == 403, f"{metodo} {url}"

    # El mismo revisor sí entra a lo que le corresponde.
    assert (await cliente.get("/api/licitaciones", headers=revisor.cabeceras)).status_code == 200
