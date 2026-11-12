"""Almacén de objetos AWS S3 (C-07, ADR-005).

Credenciales: rol IAM en la VM; nunca claves en el código ni en el repositorio (S-06).
Objetos bajo prefijo de organización con nombre aleatorio (S-09).
boto3 es síncrono: las operaciones de red se ejecutan en un hilo.
"""

import asyncio
import uuid
from functools import lru_cache
from typing import TYPE_CHECKING

import boto3

from analizador.core.config import get_settings

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client


@lru_cache
def _client() -> "S3Client":
    return boto3.client("s3", region_name=get_settings().aws_region)


def nueva_clave_pliego(organizacion_id: uuid.UUID) -> str:
    return f"organizaciones/{organizacion_id}/pliegos/{uuid.uuid4()}.pdf"


async def subir_pdf(clave: str, contenido: bytes) -> None:
    await asyncio.to_thread(
        _client().put_object,
        Bucket=get_settings().s3_bucket,
        Key=clave,
        Body=contenido,
        ContentType="application/pdf",
        ServerSideEncryption="AES256",
    )


async def descargar(clave: str) -> bytes:
    def _get() -> bytes:
        respuesta = _client().get_object(Bucket=get_settings().s3_bucket, Key=clave)
        return respuesta["Body"].read()

    return await asyncio.to_thread(_get)


def url_temporal(clave: str) -> str:
    """URL prefirmada de corta duración para el visor del frontend (P-14, S-10, RF-26).

    Solo se emite después de verificar que la licitación pertenece a la organización (S-03).
    """
    settings = get_settings()
    return _client().generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.s3_bucket, "Key": clave},
        ExpiresIn=settings.s3_presign_ttl_seconds,
    )
