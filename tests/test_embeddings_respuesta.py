"""Contrato de los vectores antes de almacenarlos en pgvector."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from analizador.core.config import get_settings
from analizador.integrations import nvidia


@pytest.mark.parametrize(
    "indices,vectores",
    [
        ([0, 0], [[1.0], [1.0]]),
        ([0], [[1.0]]),
        ([0, 1], [[], []]),
        ([0, 1], [[1.0], [1.0, 2.0]]),
        ([0, 1], [[float("nan")], [1.0]]),
    ],
)
async def test_rechaza_vectores_incompletos_o_invalidos(
    monkeypatch: pytest.MonkeyPatch, indices: list[int], vectores: list[list[float]]
) -> None:
    monkeypatch.setenv("NVIDIA_MODEL_EMBEDDINGS", "modelo-prueba")
    get_settings.cache_clear()
    respuesta = SimpleNamespace(
        data=[SimpleNamespace(index=i, embedding=v) for i, v in zip(indices, vectores, strict=True)]
    )
    cliente = SimpleNamespace(embeddings=SimpleNamespace(create=AsyncMock(return_value=respuesta)))
    monkeypatch.setattr(nvidia, "get_client", lambda: cliente)
    with pytest.raises(RuntimeError):
        await nvidia.embeddings(["primero", "segundo"], "passage")


async def test_reordena_la_respuesta_por_el_indice_del_proveedor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NVIDIA_MODEL_EMBEDDINGS", "modelo-prueba")
    get_settings.cache_clear()
    respuesta = SimpleNamespace(
        data=[SimpleNamespace(index=1, embedding=[2.0]), SimpleNamespace(index=0, embedding=[1.0])]
    )
    monkeypatch.setattr(
        nvidia,
        "get_client",
        lambda: SimpleNamespace(
            embeddings=SimpleNamespace(create=AsyncMock(return_value=respuesta))
        ),
    )
    assert await nvidia.embeddings(["primero", "segundo"], "passage") == [[1.0], [2.0]]
