"""HU-B29: neutralización de fórmulas en la exportación (S-12)."""

import pytest

from analizador.api.exportacion import neutralizar


@pytest.mark.parametrize("valor", ["=1+1", "+1", "-1", "@SUM(A1)", "\t=1", "\r=1"])
def test_los_textos_peligrosos_se_anteponen_con_apostrofo(valor: str) -> None:
    assert neutralizar(valor) == "'" + valor


@pytest.mark.parametrize("valor", ["Texto normal", "3.2.1", "", 7, None, "1-2"])
def test_los_demas_valores_no_cambian(valor: str | int | None) -> None:
    assert neutralizar(valor) == valor
