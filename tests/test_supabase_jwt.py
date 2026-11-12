"""S-04: verificación de firma, expiración, emisor y audiencia del token de Supabase."""

import time
import uuid
from dataclasses import dataclass
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from analizador.integrations import supabase

EMISOR = "https://proyecto-test.supabase.co/auth/v1"
CLAVE_PRIVADA = ec.generate_private_key(ec.SECP256R1())
OTRA_CLAVE = ec.generate_private_key(ec.SECP256R1())


@dataclass
class _ClaveFirma:
    key: Any


class _JwksFalso:
    def get_signing_key_from_jwt(self, token: str) -> _ClaveFirma:
        return _ClaveFirma(CLAVE_PRIVADA.public_key())


@pytest.fixture(autouse=True)
def jwks_falso(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(supabase, "_jwks_client", lambda: _JwksFalso())


def _token(clave: Any = CLAVE_PRIVADA, **claims: Any) -> str:
    base = {
        "sub": str(uuid.uuid4()),
        "email": "revisor@ejemplo.co",
        "aud": "authenticated",
        "iss": EMISOR,
        "exp": int(time.time()) + 300,
    }
    base.update(claims)
    return jwt.encode(base, clave, algorithm="ES256")


async def test_token_valido() -> None:
    usuario_id = uuid.uuid4()
    usuario = await supabase.verificar_token(_token(sub=str(usuario_id)))
    assert usuario.usuario_id == usuario_id
    assert usuario.email == "revisor@ejemplo.co"


@pytest.mark.parametrize(
    "claims",
    [
        {"exp": int(time.time()) - 10},
        {"aud": "otra-audiencia"},
        {"iss": "https://otro.supabase.co/auth/v1"},
        {"sub": "no-es-uuid"},
    ],
)
async def test_token_rechazado(claims: dict[str, Any]) -> None:
    with pytest.raises(supabase.TokenInvalidoError):
        await supabase.verificar_token(_token(**claims))


async def test_firma_de_otra_clave_rechazada() -> None:
    with pytest.raises(supabase.TokenInvalidoError):
        await supabase.verificar_token(_token(clave=OTRA_CLAVE))


async def test_hs256_rechazado() -> None:
    token = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "aud": "authenticated",
            "iss": EMISOR,
            "exp": int(time.time()) + 60,
        },
        "secreto-compartido-de-al-menos-32-bytes!!",
        algorithm="HS256",
    )
    with pytest.raises(supabase.TokenInvalidoError):
        await supabase.verificar_token(token)
