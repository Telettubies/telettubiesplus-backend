"""Utilidades compartidas por las pruebas: tokens de Supabase firmados, dobles y fábricas."""

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy.ext.asyncio import AsyncSession

from analizador.integrations import almacenamiento
from analizador.models import (
    Fragmento,
    FuenteConocimiento,
    Item,
    Licitacion,
    Miembro,
    Organizacion,
    Requisito,
    Resultado,
    Seccion,
    TrabajoProcesamiento,
)
from analizador.models.enums import (
    EstadoCumplimiento,
    EstadoLicitacion,
    EstadoTrabajo,
    OrigenFragmento,
    Rol,
    TipoFuenteConocimiento,
    TipoRequisito,
    TipoTrabajo,
)

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


@dataclass
class AlmacenFalso:
    """Sustituye a Supabase Storage: guarda los objetos en memoria y registra las firmas."""

    objetos: dict[str, bytes] = field(default_factory=dict)
    firmadas: list[str] = field(default_factory=list)
    eliminadas: list[str] = field(default_factory=list)
    fallar_al_subir: bool = False

    async def subir_pdf(self, clave: str, contenido: bytes) -> None:
        if self.fallar_al_subir:
            raise almacenamiento.AlmacenamientoError("No se pudo guardar el archivo")
        self.objetos[clave] = contenido

    async def descargar(self, clave: str) -> bytes:
        return self.objetos[clave]

    async def eliminar(self, clave: str) -> None:
        self.eliminadas.append(clave)
        self.objetos.pop(clave, None)

    async def url_temporal(self, clave: str) -> str:
        self.firmadas.append(clave)
        return f"https://almacen.test/firmada/{clave}?token=prueba"

    def instalar(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for nombre in ("subir_pdf", "descargar", "eliminar", "url_temporal"):
            monkeypatch.setattr(almacenamiento, nombre, getattr(self, nombre))


@dataclass(frozen=True)
class Matriz:
    """Identificadores de una licitación sembrada con una fila de matriz completa."""

    licitacion_id: uuid.UUID
    seccion_id: uuid.UUID
    requisito_id: uuid.UUID
    item_id: uuid.UUID
    resultado_id: uuid.UUID
    trabajo_id: uuid.UUID
    fuente_id: uuid.UUID
    fragmento_id: uuid.UUID
    storage_key: str

    @property
    def ids(self) -> list[uuid.UUID]:
        return [
            self.licitacion_id,
            self.seccion_id,
            self.requisito_id,
            self.item_id,
            self.resultado_id,
            self.trabajo_id,
            self.fuente_id,
            self.fragmento_id,
        ]


async def sembrar_matriz(sesion: AsyncSession, quien: Identidad, marca: str) -> Matriz:
    """Crea una licitación con sección, requisito, ítem, resultado, trabajo y conocimiento.

    `marca` se incluye en todos los textos para poder buscarla en las respuestas de la API.
    """
    clave = almacenamiento.nueva_clave_pliego(quien.organizacion_id)
    licitacion = Licitacion(
        organizacion_id=quien.organizacion_id,
        creado_por=quien.usuario_id,
        titulo=f"Licitación {marca}",
        nombre_archivo=f"{marca}.pdf",
        storage_key=clave,
        estado=EstadoLicitacion.COMPLETADA,
        recomendacion="presentarse",
        justificacion=f"Justificación {marca}",
    )
    sesion.add(licitacion)
    await sesion.flush()
    seccion = Seccion(
        licitacion_id=licitacion.id,
        orden=1,
        numeral="3.1",
        titulo=f"Sección {marca}",
        pagina_inicio=1,
        pagina_fin=2,
        texto=f"Texto de la sección {marca}",
    )
    sesion.add(seccion)
    await sesion.flush()
    requisito = Requisito(
        licitacion_id=licitacion.id,
        seccion_id=seccion.id,
        numeral="3.1.1",
        tipo=TipoRequisito.HABILITANTE,
        texto_literal=f"Requisito {marca}",
    )
    sesion.add(requisito)
    await sesion.flush()
    item = Item(requisito_id=requisito.id, orden=1, descripcion=f"Ítem {marca}")
    sesion.add(item)
    await sesion.flush()
    resultado = Resultado(
        item_id=item.id,
        estado=EstadoCumplimiento.NO_CUMPLE,
        evidencia=[{"texto": f"Evidencia {marca}"}],
        faltante=f"Faltante {marca}",
    )
    trabajo = TrabajoProcesamiento(
        organizacion_id=quien.organizacion_id,
        tipo=TipoTrabajo.ANALISIS,
        licitacion_id=licitacion.id,
        estado=EstadoTrabajo.FALLIDO,
        error=f"Error {marca}",
    )
    fuente = FuenteConocimiento(
        organizacion_id=quien.organizacion_id,
        tipo=TipoFuenteConocimiento.CONTRATO,
        titulo=f"Contrato {marca}",
        datos={"objeto": f"Objeto {marca}"},
    )
    sesion.add_all([resultado, trabajo, fuente])
    await sesion.flush()
    fragmento = Fragmento(
        organizacion_id=quien.organizacion_id,
        origen=OrigenFragmento.EMPRESA,
        fuente_conocimiento_id=fuente.id,
        texto=f"Fragmento {marca}",
        embedding=[1.0, 0.0, 0.0],
    )
    sesion.add(fragmento)
    await sesion.commit()
    return Matriz(
        licitacion_id=licitacion.id,
        seccion_id=seccion.id,
        requisito_id=requisito.id,
        item_id=item.id,
        resultado_id=resultado.id,
        trabajo_id=trabajo.id,
        fuente_id=fuente.id,
        fragmento_id=fragmento.id,
        storage_key=clave,
    )
