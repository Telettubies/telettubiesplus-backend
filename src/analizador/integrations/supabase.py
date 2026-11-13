"""Supabase Auth (C-08, ADR-008).

- Verificación de tokens con las claves asimétricas del proyecto (JWKS). S-04: firma,
  expiración, emisor y audiencia en cada petición.
- Invitación de usuarios con la service role key, solo desde el backend (F-01, S-06).
"""

import asyncio
import uuid
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import httpx
import jwt

from analizador.core.config import get_settings

ALGORITMOS_PERMITIDOS = ["ES256", "RS256", "EdDSA"]


class TokenInvalidoError(Exception):
    pass


class UsuarioExistenteError(Exception):
    """El correo ya tiene una cuenta en Supabase: la invitación no la crea de nuevo."""


class SupabaseError(Exception):
    pass


@dataclass(frozen=True)
class UsuarioAutenticado:
    usuario_id: uuid.UUID
    email: str | None
    # True si la sesión demuestra el control del correo (Google, enlace de invitación o de
    # acceso): un método distinto de una contraseña, que cualquiera puede inventar al registrarse.
    correo_comprobado: bool = False


@lru_cache
def _jwks_client() -> jwt.PyJWKClient:
    # Cachea las claves; solo vuelve a pedirlas ante un `kid` desconocido.
    return jwt.PyJWKClient(get_settings().supabase_jwks_url, cache_keys=True, lifespan=600)


def _decodificar(token: str, clave: Any) -> dict[str, Any]:
    settings = get_settings()
    return jwt.decode(
        token,
        key=clave,
        algorithms=ALGORITMOS_PERMITIDOS,
        audience=settings.supabase_jwt_audience,
        issuer=settings.supabase_issuer,
        options={"require": ["exp", "sub", "aud", "iss"]},
    )


async def verificar_token(token: str) -> UsuarioAutenticado:
    try:
        # PyJWKClient hace E/S bloqueante cuando no tiene la clave en caché.
        clave = await asyncio.to_thread(_jwks_client().get_signing_key_from_jwt, token)
        claims = _decodificar(token, clave.key)
        metodos = [m.get("method") for m in claims.get("amr") or [] if isinstance(m, dict)]
        return UsuarioAutenticado(
            usuario_id=uuid.UUID(claims["sub"]),
            email=claims.get("email"),
            correo_comprobado=bool(metodos) and all(m != "password" for m in metodos),
        )
    except (jwt.PyJWTError, ValueError, KeyError) as exc:
        raise TokenInvalidoError(str(exc)) from exc


async def invitar_usuario(email: str) -> uuid.UUID:
    """Invita un usuario por correo y devuelve su id de Supabase.

    Si el correo ya tiene cuenta (por ejemplo, pertenece a otra organización), Supabase no lo
    invita de nuevo: se lanza `UsuarioExistenteError`. Pendiente de validar con el proyecto real.
    """
    settings = get_settings()
    if settings.supabase_service_role_key is None:
        raise SupabaseError("SUPABASE_SERVICE_ROLE_KEY no está configurada")
    clave = settings.supabase_service_role_key.get_secret_value()
    async with httpx.AsyncClient(timeout=15) as cliente:
        respuesta = await cliente.post(
            f"{settings.supabase_issuer}/invite",
            headers={"apikey": clave, "Authorization": f"Bearer {clave}"},
            json={"email": email},
        )
    if respuesta.status_code == 422 and _codigo_de_error(respuesta) == "email_exists":
        raise UsuarioExistenteError("El correo ya tiene una cuenta")
    if respuesta.status_code >= 400:
        # No se reenvía el cuerpo completo al cliente (puede contener detalles internos).
        raise SupabaseError(f"Supabase respondió {respuesta.status_code} al invitar")
    return uuid.UUID(respuesta.json()["id"])


def _codigo_de_error(respuesta: httpx.Response) -> str | None:
    try:
        cuerpo = respuesta.json()
    except ValueError:
        return None
    codigo = cuerpo.get("error_code") if isinstance(cuerpo, dict) else None
    return codigo if isinstance(codigo, str) else None


async def id_de_usuario_existente(email: str) -> uuid.UUID:
    """Id de Supabase de un correo que ya tiene cuenta, sin enviarle ningún correo.

    `generate_link` devuelve el usuario y el enlace, pero no lo envía.
    """
    settings = get_settings()
    if settings.supabase_service_role_key is None:
        raise SupabaseError("SUPABASE_SERVICE_ROLE_KEY no está configurada")
    clave = settings.supabase_service_role_key.get_secret_value()
    async with httpx.AsyncClient(timeout=15) as cliente:
        respuesta = await cliente.post(
            f"{settings.supabase_issuer}/admin/generate_link",
            headers={"apikey": clave, "Authorization": f"Bearer {clave}"},
            json={"type": "magiclink", "email": email},
        )
    if respuesta.status_code >= 400:
        raise SupabaseError(f"Supabase respondió {respuesta.status_code} al buscar el usuario")
    datos = respuesta.json()
    return uuid.UUID(str(datos.get("id") or datos["user"]["id"]))


async def eliminar_usuario(usuario_id: uuid.UUID) -> None:
    """Borra un usuario recién invitado cuando no se pudo crear su membresía (R-15)."""
    settings = get_settings()
    if settings.supabase_service_role_key is None:
        raise SupabaseError("SUPABASE_SERVICE_ROLE_KEY no está configurada")
    clave = settings.supabase_service_role_key.get_secret_value()
    async with httpx.AsyncClient(timeout=15) as cliente:
        respuesta = await cliente.delete(
            f"{settings.supabase_issuer}/admin/users/{usuario_id}",
            headers={"apikey": clave, "Authorization": f"Bearer {clave}"},
        )
    if respuesta.status_code >= 400 and respuesta.status_code != 404:
        raise SupabaseError(f"Supabase respondió {respuesta.status_code} al eliminar")
