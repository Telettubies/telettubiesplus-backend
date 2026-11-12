"""S-08 y S-09: límite de tamaño y verificación del tipo real del archivo."""

import io

import pytest
from fastapi import HTTPException, UploadFile

from analizador.api.routers.licitaciones import _leer_pdf


def _archivo(contenido: bytes) -> UploadFile:
    return UploadFile(io.BytesIO(contenido), filename="pliego.pdf")


async def test_pdf_valido() -> None:
    assert await _leer_pdf(_archivo(b"%PDF-1.7\n...")) == b"%PDF-1.7\n..."


async def test_archivo_que_no_es_pdf() -> None:
    with pytest.raises(HTTPException) as exc:
        await _leer_pdf(_archivo(b"MZ\x90\x00 ejecutable con extension .pdf"))
    assert exc.value.status_code == 415


async def test_pdf_demasiado_grande() -> None:
    # MAX_PDF_MB=1 en conftest
    with pytest.raises(HTTPException) as exc:
        await _leer_pdf(_archivo(b"%PDF-" + b"0" * (1024 * 1024)))
    assert exc.value.status_code == 413
