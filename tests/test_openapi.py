"""El contrato publicado para el frontend (docs/openapi.json) coincide con la API."""

import json
from pathlib import Path

from analizador.api.main import create_app
from analizador.cli import exportar_openapi

CONTRATO = Path(__file__).resolve().parents[1] / "docs" / "openapi.json"


def test_contrato_openapi_al_dia() -> None:
    actual = json.loads(json.dumps(create_app().openapi()))
    publicado = json.loads(CONTRATO.read_text(encoding="utf-8"))
    assert publicado == actual, "Contrato desactualizado: uv run analizador-cli exportar-openapi"


def test_exportar_openapi_escribe_el_contrato(tmp_path: Path) -> None:
    salida = tmp_path / "sub" / "openapi.json"
    exportar_openapi(salida)
    assert (
        json.loads(salida.read_text(encoding="utf-8"))["info"]["title"] == "Analizador de pliegos"
    )
