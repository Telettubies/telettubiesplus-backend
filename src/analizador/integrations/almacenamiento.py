"""Almacén de objetos (C-07): Supabase Storage por su API REST.

Sustituye a AWS S3 (ADR-005) por decisión del equipo; ver docs/PLAN.md, sección 4.

- El bucket es privado: los PDF solo salen por URL firmadas de corta duración (S-10, P-14).
- La clave de servicio se usa únicamente aquí, en el backend (S-06).
- Los objetos van bajo el prefijo de su organización, con nombre aleatorio (S-09, HU-B04).
"""

import uuid
from typing import Any
from urllib.parse import quote

import httpx

from analizador.core.config import get_settings

TIEMPO_ESPERA = httpx.Timeout(60.0, connect=10.0)


class AlmacenamientoError(Exception):
    """Fallo al hablar con el almacén. El mensaje no incluye la respuesta del proveedor."""


def prefijo_organizacion(organizacion_id: uuid.UUID) -> str:
    return f"organizaciones/{organizacion_id}/"


def nueva_clave_pliego(organizacion_id: uuid.UUID) -> str:
    return f"{prefijo_organizacion(organizacion_id)}pliegos/{uuid.uuid4()}.pdf"


def pertenece_a(clave: str, organizacion_id: uuid.UUID) -> bool:
    """Segunda barrera de S-03: la clave debe estar bajo el prefijo de la organización."""
    return clave.startswith(prefijo_organizacion(organizacion_id))


def _base() -> str:
    return f"{get_settings().supabase_url}/storage/v1"


def _ruta(clave: str) -> str:
    return f"{quote(get_settings().supabase_storage_bucket)}/{quote(clave)}"


def _cabeceras() -> dict[str, str]:
    clave = get_settings().supabase_service_role_key
    if clave is None:
        raise AlmacenamientoError("SUPABASE_SERVICE_ROLE_KEY no está configurada")
    secreto = clave.get_secret_value()
    return {"apikey": secreto, "Authorization": f"Bearer {secreto}"}


def _cliente() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=TIEMPO_ESPERA)


async def _pedir(
    metodo: str,
    url: str,
    operacion: str,
    *,
    contenido: bytes | None = None,
    cuerpo: dict[str, Any] | None = None,
    cabeceras: dict[str, str] | None = None,
) -> httpx.Response:
    try:
        async with _cliente() as cliente:
            respuesta = await cliente.request(
                metodo,
                url,
                headers={**_cabeceras(), **(cabeceras or {})},
                content=contenido,
                json=cuerpo,
            )
    except httpx.HTTPError as exc:
        raise AlmacenamientoError(f"No se pudo {operacion}: error de red") from exc
    if respuesta.status_code >= 400:
        raise AlmacenamientoError(
            f"No se pudo {operacion}: el almacén respondió {respuesta.status_code}"
        )
    return respuesta


async def subir_pdf(clave: str, contenido: bytes) -> None:
    await _pedir(
        "POST",
        f"{_base()}/object/{_ruta(clave)}",
        "guardar el archivo",
        contenido=contenido,
        cabeceras={"Content-Type": "application/pdf", "x-upsert": "false"},
    )


async def descargar(clave: str) -> bytes:
    respuesta = await _pedir("GET", f"{_base()}/object/{_ruta(clave)}", "leer el archivo")
    return respuesta.content


async def eliminar(clave: str) -> None:
    bucket = quote(get_settings().supabase_storage_bucket)
    await _pedir(
        "DELETE",
        f"{_base()}/object/{bucket}",
        "eliminar el archivo",
        cuerpo={"prefixes": [clave]},
    )


async def url_temporal(clave: str) -> str:
    """URL firmada de corta duración para el visor del frontend (P-14, S-10, RF-26).

    Quien la pide debe haber verificado antes que el objeto es de la organización (S-03).
    """
    respuesta = await _pedir(
        "POST",
        f"{_base()}/object/sign/{_ruta(clave)}",
        "firmar la URL del archivo",
        cuerpo={"expiresIn": get_settings().storage_url_ttl_seconds},
    )
    try:
        firmada = respuesta.json()["signedURL"]
    except (ValueError, KeyError, TypeError) as exc:
        raise AlmacenamientoError("No se pudo firmar la URL del archivo") from exc
    return f"{_base()}/{str(firmada).lstrip('/')}"
