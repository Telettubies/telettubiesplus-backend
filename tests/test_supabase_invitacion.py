"""Invitaciones en la API de administración de Supabase (HU-B02): errores y compensación."""

import uuid
from collections.abc import Callable

import httpx
import pytest

from analizador.integrations import supabase

_AsyncClient = httpx.AsyncClient


def _responder(
    monkeypatch: pytest.MonkeyPatch, manejador: Callable[[httpx.Request], httpx.Response]
) -> list[httpx.Request]:
    peticiones: list[httpx.Request] = []

    def transporte(peticion: httpx.Request) -> httpx.Response:
        peticiones.append(peticion)
        return manejador(peticion)

    def fabrica(**kwargs: object) -> httpx.AsyncClient:
        return _AsyncClient(transport=httpx.MockTransport(transporte), **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(supabase.httpx, "AsyncClient", fabrica)
    return peticiones


async def test_la_invitacion_devuelve_el_id_del_usuario(monkeypatch: pytest.MonkeyPatch) -> None:
    usuario_id = uuid.uuid4()
    peticiones = _responder(
        monkeypatch, lambda _: httpx.Response(200, json={"id": str(usuario_id)})
    )

    assert await supabase.invitar_usuario("nueva@empresa.co") == usuario_id
    [peticion] = peticiones
    assert peticion.url.path == "/auth/v1/invite"
    assert peticion.headers["apikey"] == "clave-de-servicio-de-prueba"


async def test_un_correo_existente_se_distingue_de_otros_errores(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _responder(
        monkeypatch,
        lambda _: httpx.Response(422, json={"error_code": "email_exists", "msg": "detalle"}),
    )
    with pytest.raises(supabase.UsuarioExistenteError):
        await supabase.invitar_usuario("existe@empresa.co")

    _responder(monkeypatch, lambda _: httpx.Response(422, text="no es json"))
    with pytest.raises(supabase.SupabaseError):
        await supabase.invitar_usuario("otra@empresa.co")


async def test_eliminar_un_usuario_usa_la_api_de_administracion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    usuario_id = uuid.uuid4()
    peticiones = _responder(monkeypatch, lambda _: httpx.Response(200, json={}))

    await supabase.eliminar_usuario(usuario_id)

    [peticion] = peticiones
    assert peticion.method == "DELETE"
    assert peticion.url.path == f"/auth/v1/admin/users/{usuario_id}"


async def test_eliminar_un_usuario_que_ya_no_existe_no_es_un_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _responder(monkeypatch, lambda _: httpx.Response(404, json={}))
    await supabase.eliminar_usuario(uuid.uuid4())

    _responder(monkeypatch, lambda _: httpx.Response(500, json={}))
    with pytest.raises(supabase.SupabaseError):
        await supabase.eliminar_usuario(uuid.uuid4())
