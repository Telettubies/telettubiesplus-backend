"""Utilidades compartidas por las pruebas: tokens de Supabase firmados, dobles y fábricas."""

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import jwt
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy.ext.asyncio import AsyncSession

from analizador.models import Miembro, Organizacion
from analizador.models.enums import Rol, TipoTrabajo

EMISOR = "https://proyecto-test.supabase.co/auth/v1"
CLAVE_PRIVADA = ec.generate_private_key(ec.SECP256R1())
OTRA_CLAVE = ec.generate_private_key(ec.SECP256R1())


@dataclass
class _ClaveFirma:
    key: Any


class JwksFalso:
    """Sustituye al cliente JWKS: devuelve siempre la clave pública de las pruebas."""

    def get_signing_key_from_jwt(self, token: str) -> _ClaveFirma:
        return _ClaveFirma(CLAVE_PRIVADA.public_key())


def token(usuario_id: uuid.UUID | None = None, clave: Any = CLAVE_PRIVADA, **claims: Any) -> str:
    base: dict[str, Any] = {
        "sub": str(usuario_id or uuid.uuid4()),
        "email": "usuario@ejemplo.co",
        "aud": "authenticated",
        "iss": EMISOR,
        "exp": int(time.time()) + 300,
    }
    base.update(claims)
    return jwt.encode(base, clave, algorithm="ES256")


def cabeceras(usuario_id: uuid.UUID, **claims: Any) -> dict[str, str]:
    return {"Authorization": f"Bearer {token(usuario_id, **claims)}"}


@dataclass
class PublicadorFalso:
    """Registra lo publicado en lugar de enviarlo a RabbitMQ."""

    publicados: list[tuple[TipoTrabajo, uuid.UUID]] = field(default_factory=list)
    conectado: bool = True
    fallar: bool = False

    async def publicar(self, tipo: TipoTrabajo, trabajo_id: uuid.UUID) -> None:
        if self.fallar:
            raise ConnectionError("cola no disponible")
        self.publicados.append((tipo, trabajo_id))


@dataclass(frozen=True)
class Identidad:
    """Un usuario autenticado con su membresía en una organización."""

    organizacion_id: uuid.UUID
    usuario_id: uuid.UUID
    miembro_id: uuid.UUID
    rol: Rol

    @property
    def cabeceras(self) -> dict[str, str]:
        return cabeceras(self.usuario_id)


async def agregar_miembro(
    sesion: AsyncSession,
    organizacion_id: uuid.UUID,
    rol: Rol = Rol.REVISOR,
    usuario_id: uuid.UUID | None = None,
) -> Identidad:
    miembro = Miembro(
        organizacion_id=organizacion_id,
        usuario_id=usuario_id or uuid.uuid4(),
        email=f"{uuid.uuid4().hex[:8]}@ejemplo.co",
        rol=rol,
    )
    sesion.add(miembro)
    await sesion.commit()
    return Identidad(organizacion_id, miembro.usuario_id, miembro.id, rol)


async def crear_organizacion(
    sesion: AsyncSession, nombre: str = "Organización A", rol: Rol = Rol.ADMINISTRADOR
) -> Identidad:
    """Crea una organización con un primer miembro del rol indicado."""
    organizacion = Organizacion(nombre=nombre)
    sesion.add(organizacion)
    await sesion.flush()
    return await agregar_miembro(sesion, organizacion.id, rol)
