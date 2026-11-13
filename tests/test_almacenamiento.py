"""T-01: Supabase Storage por su API REST (sustituye a S3; S-06, S-09, S-10, P-14)."""

import json
import uuid
from collections.abc import Callable

import httpx
import pytest

from analizador.core.config import get_settings
from analizador.integrations import almacenamiento

BASE = "https://proyecto-test.supabase.co/storage/v1"
CLAVE = "organizaciones/11111111-1111-1111-1111-111111111111/pliegos/abc.pdf"

Manejador = Callable[[httpx.Request], httpx.Response]


@pytest.fixture
def almacen(monkeypatch: pytest.MonkeyPatch) -> Callable[[Manejador], list[httpx.Request]]:
    """Sustituye el transporte HTTP y devuelve las peticiones recibidas."""

    def instalar(manejador: Manejador) -> list[httpx.Request]:
        recibidas: list[httpx.Request] = []

        def registrar(peticion: httpx.Request) -> httpx.Response:
            recibidas.append(peticion)
            return manejador(peticion)

        monkeypatch.setattr(
            almacenamiento,
            "_cliente",
            lambda: httpx.AsyncClient(transport=httpx.MockTransport(registrar)),
        )
        return recibidas

    return instalar


def test_la_clave_va_bajo_el_prefijo_de_la_organizacion_con_nombre_aleatorio() -> None:
    organizacion = uuid.uuid4()
    primera = almacenamiento.nueva_clave_pliego(organizacion)
    segunda = almacenamiento.nueva_clave_pliego(organizacion)

    assert primera.startswith(f"organizaciones/{organizacion}/pliegos/")
    assert primera.endswith(".pdf")
    assert primera != segunda
    assert almacenamiento.pertenece_a(primera, organizacion)
    assert not almacenamiento.pertenece_a(primera, uuid.uuid4())


async def test_subir_envia_el_pdf_con_la_clave_de_servicio_y_sin_sobrescribir(
    almacen: Callable[[Manejador], list[httpx.Request]],
) -> None:
    recibidas = almacen(lambda _: httpx.Response(200, json={"Key": CLAVE}))

    await almacenamiento.subir_pdf(CLAVE, b"%PDF-1.7 contenido")

    [peticion] = recibidas
    assert peticion.method == "POST"
    assert str(peticion.url) == f"{BASE}/object/pliegos-test/{CLAVE}"
    assert peticion.content == b"%PDF-1.7 contenido"
    assert peticion.headers["content-type"] == "application/pdf"
    assert peticion.headers["x-upsert"] == "false"
    assert peticion.headers["authorization"] == "Bearer clave-de-servicio-de-prueba"
    assert peticion.headers["apikey"] == "clave-de-servicio-de-prueba"


async def test_descargar_devuelve_los_bytes(
    almacen: Callable[[Manejador], list[httpx.Request]],
) -> None:
    recibidas = almacen(lambda _: httpx.Response(200, content=b"%PDF-bytes"))

    assert await almacenamiento.descargar(CLAVE) == b"%PDF-bytes"
    assert recibidas[0].method == "GET"
    assert str(recibidas[0].url) == f"{BASE}/object/pliegos-test/{CLAVE}"


async def test_url_temporal_pide_la_firma_con_la_vigencia_configurada(
    almacen: Callable[[Manejador], list[httpx.Request]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STORAGE_URL_TTL_SECONDS", "120")
    get_settings.cache_clear()
    firmada = f"/object/sign/pliegos-test/{CLAVE}?token=abc.def"
    recibidas = almacen(lambda _: httpx.Response(200, json={"signedURL": firmada}))

    url = await almacenamiento.url_temporal(CLAVE)

    assert url == f"{BASE}{firmada}"
    assert recibidas[0].method == "POST"
    assert str(recibidas[0].url) == f"{BASE}/object/sign/pliegos-test/{CLAVE}"
    assert json.loads(recibidas[0].content) == {"expiresIn": 120}


async def test_eliminar_envia_la_clave_como_prefijo(
    almacen: Callable[[Manejador], list[httpx.Request]],
) -> None:
    recibidas = almacen(lambda _: httpx.Response(200, json=[]))

    await almacenamiento.eliminar(CLAVE)

    assert recibidas[0].method == "DELETE"
    assert str(recibidas[0].url) == f"{BASE}/object/pliegos-test"
    assert json.loads(recibidas[0].content) == {"prefixes": [CLAVE]}


async def test_un_error_del_almacen_no_expone_su_respuesta(
    almacen: Callable[[Manejador], list[httpx.Request]],
) -> None:
    almacen(lambda _: httpx.Response(403, json={"message": "detalle interno del proveedor"}))

    with pytest.raises(almacenamiento.AlmacenamientoError) as exc:
        await almacenamiento.descargar(CLAVE)

    assert "403" in str(exc.value)
    assert "detalle interno" not in str(exc.value)


async def test_un_fallo_de_red_es_un_error_del_almacen(
    almacen: Callable[[Manejador], list[httpx.Request]],
) -> None:
    def sin_red(peticion: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("sin conexión", request=peticion)

    almacen(sin_red)

    with pytest.raises(almacenamiento.AlmacenamientoError):
        await almacenamiento.subir_pdf(CLAVE, b"%PDF-")


async def test_firma_sin_url_en_la_respuesta_es_un_error(
    almacen: Callable[[Manejador], list[httpx.Request]],
) -> None:
    almacen(lambda _: httpx.Response(200, json={"otra": "cosa"}))

    with pytest.raises(almacenamiento.AlmacenamientoError):
        await almacenamiento.url_temporal(CLAVE)


async def test_sin_clave_de_servicio_falla_antes_de_llamar(
    almacen: Callable[[Manejador], list[httpx.Request]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "")
    get_settings.cache_clear()
    recibidas = almacen(lambda _: httpx.Response(200))

    with pytest.raises(almacenamiento.AlmacenamientoError):
        await almacenamiento.subir_pdf(CLAVE, b"%PDF-")
    assert recibidas == []
