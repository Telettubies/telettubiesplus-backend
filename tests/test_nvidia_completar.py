"""Llamada de chat a NVIDIA: instrucciones y documento por separado (S-07), con reintentos."""

from types import SimpleNamespace
from typing import Any

import pytest

from analizador.integrations import nvidia
from analizador.integrations.anthropic_client import ModeloNoConfiguradoError


class _Chat:
    def __init__(self, contenido: str | None) -> None:
        self.contenido = contenido
        self.peticiones: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.peticiones.append(kwargs)
        mensaje = SimpleNamespace(content=self.contenido)
        return SimpleNamespace(choices=[SimpleNamespace(message=mensaje)])


def _instalar(monkeypatch: pytest.MonkeyPatch, contenido: str | None) -> _Chat:
    chat = _Chat(contenido)
    cliente = SimpleNamespace(chat=SimpleNamespace(completions=chat))
    monkeypatch.setattr(nvidia, "get_client", lambda: cliente)
    return chat


async def test_envia_las_instrucciones_y_el_documento_en_mensajes_separados(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chat = _instalar(monkeypatch, '{"secciones": []}')

    respuesta = await nvidia.completar(
        modelo="modelo-de-filtro",
        variable="NVIDIA_MODEL_FILTRO",
        sistema="instrucciones",
        usuario="<seccion>texto del pliego</seccion>",
        max_tokens=500,
    )

    assert respuesta == '{"secciones": []}'
    [peticion] = chat.peticiones
    assert peticion["model"] == "modelo-de-filtro"
    assert peticion["messages"] == [
        {"role": "system", "content": "instrucciones"},
        {"role": "user", "content": "<seccion>texto del pliego</seccion>"},
    ]
    assert peticion["temperature"] == 0 and peticion["max_tokens"] == 500


async def test_una_respuesta_sin_contenido_es_una_cadena_vacia(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _instalar(monkeypatch, None)
    assert await nvidia.completar(modelo="m", variable="V", sistema="s", usuario="u") == ""


async def test_sin_modelo_configurado_no_se_llama_al_proveedor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chat = _instalar(monkeypatch, "x")
    with pytest.raises(ModeloNoConfiguradoError, match="NVIDIA_MODEL_FILTRO"):
        await nvidia.completar(
            modelo=None, variable="NVIDIA_MODEL_FILTRO", sistema="s", usuario="u"
        )
    assert chat.peticiones == []
