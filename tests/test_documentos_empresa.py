"""Las propuestas de IA se conservan como datos y exigen procedencia comprobable."""

import pytest

from analizador.pipeline.documentos import Candidato, verificar_candidatos


def test_candidato_conserva_datos_parciales_sin_inventar_campos() -> None:
    candidato = Candidato(tipo="contrato", pagina=1, cita="Contrato A", datos_json='{"id":"A"}')
    resultado = verificar_candidatos([candidato], [{"numero": 1, "texto": "Contrato A"}])
    assert resultado == [
        {"tipo": "contrato", "pagina": 1, "cita": "Contrato A", "datos": {"id": "A"}}
    ]


@pytest.mark.parametrize(
    "pagina,cita,datos",
    [
        (2, "Contrato A", "{}"),
        (1, "inventada", "{}"),
        (1, "Contrato A", "[]"),
        (1, "Contrato A", "no es JSON"),
    ],
)
def test_rechaza_citas_inventadas_y_datos_invalidos(pagina: int, cita: str, datos: str) -> None:
    with pytest.raises(ValueError):
        verificar_candidatos(
            [Candidato(tipo="contrato", pagina=pagina, cita=cita, datos_json=datos)],
            [{"numero": 1, "texto": "Contrato A"}],
        )
