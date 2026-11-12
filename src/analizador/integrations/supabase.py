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


class SupabaseError(Exception):
    pass


@dataclass(frozen=True)
class UsuarioAutenticado:
    usuario_id: uuid.UUID
    email: str | None


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
        return UsuarioAutenticado(usuario_id=uuid.UUID(claims["sub"]), email=claims.get("email"))
    except (jwt.PyJWTError, ValueError, KeyError) as exc:
        raise TokenInvalidoError(str(exc)) from exc


async def invitar_usuario(email: str) -> uuid.UUID:
    """Invita un usuario por correo y devuelve su id de Supabase.

    Pendiente de validar con el proyecto real: comportamiento cuando el correo ya
    existe en Supabase (p. ej. un usuario que ya pertenece a otra organización).
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
    if respuesta.status_code >= 400:
        # No se reenvía el cuerpo completo al cliente (puede contener detalles internos).
        raise SupabaseError(f"Supabase respondió {respuesta.status_code} al invitar")
    return uuid.UUID(respuesta.json()["id"])
