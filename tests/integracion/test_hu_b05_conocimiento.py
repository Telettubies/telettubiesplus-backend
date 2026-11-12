"""HU-B05: cargar la base de conocimiento estructurada (RF-03, RF-10, Q-05, P-18)."""

import copy
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from analizador.core.config import get_settings
from analizador.models import Fragmento, FuenteConocimiento, TrabajoProcesamiento
from analizador.models.enums import (
    EstadoTrabajo,
    OrigenFragmento,
    Rol,
    TipoFuenteConocimiento,
    TipoTrabajo,
)
from tests.apoyo import Identidad, PublicadorFalso, agregar_miembro, crear_organizacion

EJEMPLO = json.loads(
    (Path(__file__).resolve().parents[2] / "docs/ejemplos/base_conocimiento.json").read_text(
        encoding="utf-8"
    )
)


@pytest.fixture
async def admin(sesion: AsyncSession) -> Identidad:
    return await crear_organizacion(sesion)


def _base(**cambios: Any) -> dict[str, Any]:
    base = copy.deepcopy(EJEMPLO)
    base.update(cambios)
    return base


async def _cargar(
    cliente: httpx.AsyncClient, quien: Identidad, base: dict[str, Any] | bytes
) -> httpx.Response:
    contenido = base if isinstance(base, bytes) else json.dumps(base).encode()
    return await cliente.post(
        "/api/conocimiento",
        headers=quien.cabeceras,
        files={"archivo": ("base.json", contenido, "application/json")},
    )


async def _fuentes(sesion: AsyncSession, quien: Identidad) -> list[FuenteConocimiento]:
    resultado = await sesion.scalars(
        select(FuenteConocimiento)
        .where(FuenteConocimiento.organizacion_id == quien.organizacion_id)
        .order_by(FuenteConocimiento.titulo)
    )
    return list(resultado)


# --- Criterio 1: un archivo válido se guarda asociado a la organización ----------------------


async def test_un_archivo_valido_se_guarda_asociado_a_la_organizacion(
    cliente: httpx.AsyncClient, sesion: AsyncSession, admin: Identidad
) -> None:
    respuesta = await _cargar(cliente, admin, EJEMPLO)

    assert respuesta.status_code == 202
    resumen = respuesta.json()["resumen"]
    assert resumen["cargada"] is True
    assert resumen["empresa"] == "Soluciones Andinas de Ingeniería S.A.S."
    assert (resumen["contratos"], resumen["personas"]) == (3, 2)
    assert (resumen["certificaciones"], resumen["capacidades"]) == (2, 2)
    assert resumen["anios_financieros"] == 2

    fuentes = await _fuentes(sesion, admin)
    assert len(fuentes) == 1 + 2 + 3 + 2 + 2 + 2  # empresa, financiero, contratos, personas…
    contrato = next(f for f in fuentes if f.titulo.startswith("C-2023-014"))
    assert contrato.tipo == TipoFuenteConocimiento.CONTRATO
    assert contrato.datos is not None
    assert contrato.datos["valor_cop"] == 2450000000
    assert contrato.datos["fecha_fin"] == "2024-01-31"
    assert contrato.datos["codigos_unspsc"] == ["81111500", "81112200", "43232300"]


async def test_el_ejemplo_publicado_cumple_el_contrato(
    cliente: httpx.AsyncClient, admin: Identidad
) -> None:
    """docs/ejemplos/base_conocimiento.json es la referencia para quien prepare su archivo."""
    assert (await _cargar(cliente, admin, EJEMPLO)).status_code == 202


async def test_la_base_la_ven_todos_los_miembros_de_la_organizacion(
    cliente: httpx.AsyncClient, sesion: AsyncSession, admin: Identidad
) -> None:
    revisor = await agregar_miembro(sesion, admin.organizacion_id, Rol.REVISOR)
    await _cargar(cliente, admin, EJEMPLO)

    resumen = await cliente.get("/api/conocimiento", headers=revisor.cabeceras)
    fuentes = await cliente.get("/api/conocimiento/fuentes", headers=revisor.cabeceras)

    assert resumen.status_code == 200 and resumen.json()["contratos"] == 3
    assert len(fuentes.json()) == 12


async def test_sin_base_cargada_el_resumen_lo_indica(
    cliente: httpx.AsyncClient, admin: Identidad
) -> None:
    resumen = (await cliente.get("/api/conocimiento", headers=admin.cabeceras)).json()
    assert resumen["cargada"] is False
    assert resumen["indexacion"] is None and resumen["contratos"] == 0


# --- Criterio 2: un archivo inválido se rechaza entero, con todos los errores ---------------


async def test_un_archivo_invalido_se_rechaza_entero_indicando_cada_campo(
    cliente: httpx.AsyncClient,
    sesion: AsyncSession,
    admin: Identidad,
    publicador: PublicadorFalso,
) -> None:
    base = _base()
    base["empresa"]["nit"] = "no-es-un-nit"
    base["financiero"][0]["indice_liquidez"] = "alto"
    base["contratos"][0]["valor_cop"] = -5
    base["contratos"][0]["codigos_unspsc"][1] = "8111"
    base["contratos"][1]["fecha_fin"] = "2020-01-01"  # anterior a su inicio
    base["certificaciones"][0]["fecha_expedicion"] = "10/05/2024"
    del base["personas"][1]["nombre"]
    base["capacidades"][0]["sector"] = "campo que no existe"

    respuesta = await _cargar(cliente, admin, base)

    assert respuesta.status_code == 422
    detalle = respuesta.json()["detail"]
    assert "No se guardó nada" in detalle["mensaje"]
    errores = {e["campo"]: e for e in detalle["errores"]}
    assert errores.keys() == {
        "empresa.nit",
        "financiero[0].indice_liquidez",
        "contratos[0].valor_cop",
        "contratos[0].codigos_unspsc[1]",
        "contratos[1]",
        "certificaciones[0].fecha_expedicion",
        "personas[1].nombre",
        "capacidades[0].sector",
    }
    assert errores["contratos[0].codigos_unspsc[1]"]["ubicacion"] == [
        "contratos",
        0,
        "codigos_unspsc",
        1,
    ]
    assert "UNSPSC de 8 dígitos" in errores["contratos[0].codigos_unspsc[1]"]["mensaje"]
    assert "NIT" in errores["empresa.nit"]["mensaje"]
    assert errores["personas[1].nombre"]["mensaje"] == "Campo obligatorio"
    assert "AAAA-MM-DD" in errores["certificaciones[0].fecha_expedicion"]["mensaje"]
    assert "anterior a fecha_inicio" in errores["contratos[1]"]["mensaje"]
    # Rechazo completo: ni siquiera se guardan los registros válidos.
    assert await _fuentes(sesion, admin) == []
    assert publicador.publicados == []


@pytest.mark.parametrize(
    ("cambio", "campo", "fragmento"),
    [
        pytest.param(
            lambda b: b["financiero"].append(copy.deepcopy(b["financiero"][0])),
            "financiero",
            "año repetido: 2025",
            id="anio-repetido",
        ),
        pytest.param(
            lambda b: b["contratos"][2].update(id="C-2023-014"),
            "contratos",
            "id de contrato repetido: C-2023-014",
            id="contrato-repetido",
        ),
        pytest.param(
            lambda b: b["financiero"][0].update(indice_endeudamiento=55),
            "financiero[0].indice_endeudamiento",
            "menor o igual que 1",
            id="endeudamiento-como-porcentaje",
        ),
        pytest.param(
            lambda b: b["contratos"][0].update(valor_cop=2450000000.5),
            "contratos[0].valor_cop",
            "número entero",
            id="valor-con-decimales",
        ),
        pytest.param(
            lambda b: b.pop("empresa"),
            "empresa",
            "Campo obligatorio",
            id="sin-empresa",
        ),
    ],
)
async def test_validacion_por_tipo_y_formato(
    cliente: httpx.AsyncClient, admin: Identidad, cambio: Any, campo: str, fragmento: str
) -> None:
    base = _base()
    cambio(base)

    respuesta = await _cargar(cliente, admin, base)

    assert respuesta.status_code == 422
    [error] = respuesta.json()["detail"]["errores"]
    assert error["campo"] == campo
    assert fragmento in error["mensaje"]


async def test_un_archivo_que_no_es_json_indica_donde_falla(
    cliente: httpx.AsyncClient, admin: Identidad
) -> None:
    respuesta = await _cargar(cliente, admin, b'{"empresa": {"razon_social": "X",}\n}')

    assert respuesta.status_code == 422
    [error] = respuesta.json()["detail"]["errores"]
    assert error["campo"] == "(archivo)"
    assert "línea 1" in error["mensaje"]


async def test_un_archivo_demasiado_grande_indica_el_limite(
    cliente: httpx.AsyncClient, admin: Identidad, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MAX_CONOCIMIENTO_MB", "1")
    get_settings.cache_clear()

    respuesta = await _cargar(cliente, admin, b" " * (1024 * 1024 + 1))

    assert respuesta.status_code == 413
    assert "1 MB" in respuesta.json()["detail"]


# --- Criterio 3: una nueva carga reemplaza la anterior por completo --------------------------


async def test_una_nueva_carga_reemplaza_la_anterior_sin_mezclar_versiones(
    cliente: httpx.AsyncClient, sesion: AsyncSession, admin: Identidad
) -> None:
    await _cargar(cliente, admin, EJEMPLO)
    [contrato] = [f for f in await _fuentes(sesion, admin) if f.titulo.startswith("C-2023-014")]
    sesion.add(
        Fragmento(
            organizacion_id=admin.organizacion_id,
            origen=OrigenFragmento.EMPRESA,
            fuente_conocimiento_id=contrato.id,
            texto="fragmento de la versión anterior",
            embedding=[1.0, 0.0],
        )
    )
    await sesion.commit()

    nueva = _base(
        contratos=[EJEMPLO["contratos"][0]], personas=[], certificaciones=[], capacidades=[]
    )
    nueva["empresa"]["razon_social"] = "Nueva Razón Social S.A.S."
    respuesta = await _cargar(cliente, admin, nueva)

    assert respuesta.status_code == 202
    resumen = respuesta.json()["resumen"]
    assert resumen["empresa"] == "Nueva Razón Social S.A.S."
    assert (resumen["contratos"], resumen["personas"], resumen["certificaciones"]) == (1, 0, 0)
    assert len(await _fuentes(sesion, admin)) == 1 + 2 + 1
    # Los fragmentos de la versión anterior desaparecen con sus fuentes.
    assert resumen["fragmentos_indexados"] == 0
    assert await sesion.scalar(select(func.count()).select_from(Fragmento)) == 0


async def test_si_la_carga_nueva_falla_se_conserva_la_anterior(
    cliente: httpx.AsyncClient,
    sesion: AsyncSession,
    admin: Identidad,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reemplazo atómico: un fallo después de borrar no deja la base vacía ni a medias."""
    await _cargar(cliente, admin, EJEMPLO)
    antes = [f.id for f in await _fuentes(sesion, admin)]

    from sqlalchemy.ext.asyncio import AsyncSession as Sesion

    async def commit_roto(self: Sesion) -> None:
        raise ConnectionError("se cayó la base de datos al confirmar")

    monkeypatch.setattr(Sesion, "commit", commit_roto)
    respuesta = await _cargar(cliente, admin, _base(contratos=[]))
    monkeypatch.undo()

    assert respuesta.status_code == 500
    sesion.expire_all()
    assert [f.id for f in await _fuentes(sesion, admin)] == antes


async def test_la_carga_de_una_organizacion_no_toca_la_de_otra(
    cliente: httpx.AsyncClient, sesion: AsyncSession, admin: Identidad
) -> None:
    otra = await crear_organizacion(sesion, "Otra organización")
    await _cargar(cliente, otra, EJEMPLO)

    await _cargar(cliente, admin, _base(contratos=[]))

    assert len(await _fuentes(sesion, otra)) == 12
    resumen = (await cliente.get("/api/conocimiento", headers=otra.cabeceras)).json()
    assert resumen["contratos"] == 3


# --- Criterio 4: tras guardar se publica un trabajo en la cola de indexación -----------------


async def test_tras_guardar_se_encola_la_indexacion(
    cliente: httpx.AsyncClient,
    sesion: AsyncSession,
    admin: Identidad,
    publicador: PublicadorFalso,
) -> None:
    respuesta = await _cargar(cliente, admin, EJEMPLO)

    trabajo = respuesta.json()["trabajo"]
    assert trabajo["tipo"] == "indexacion" and trabajo["estado"] == "en_cola"
    assert [(tipo, str(tid)) for tipo, tid in publicador.publicados] == [
        (TipoTrabajo.INDEXACION, trabajo["id"])
    ]
    guardado = await sesion.scalar(select(TrabajoProcesamiento))
    assert guardado is not None and guardado.organizacion_id == admin.organizacion_id
    assert respuesta.json()["resumen"]["indexacion"]["id"] == trabajo["id"]


async def test_si_la_cola_falla_la_base_queda_guardada_y_la_indexacion_en_error_recuperable(
    cliente: httpx.AsyncClient,
    sesion: AsyncSession,
    admin: Identidad,
    publicador: PublicadorFalso,
) -> None:
    publicador.fallar = True

    respuesta = await _cargar(cliente, admin, EJEMPLO)

    assert respuesta.status_code == 503
    assert "se guardó" in respuesta.json()["detail"]
    resumen = (await cliente.get("/api/conocimiento", headers=admin.cabeceras)).json()
    assert resumen["contratos"] == 3
    assert resumen["indexacion"]["estado"] == "fallido"
    assert resumen["indexacion"]["error"]

    publicador.fallar = False
    relanzada = await cliente.post("/api/conocimiento/indexacion", headers=admin.cabeceras)
    assert relanzada.status_code == 202
    assert publicador.publicados[-1][0] == TipoTrabajo.INDEXACION


async def test_reindexar_exige_una_base_cargada_y_ninguna_indexacion_en_curso(
    cliente: httpx.AsyncClient, sesion: AsyncSession, admin: Identidad
) -> None:
    sin_base = await cliente.post("/api/conocimiento/indexacion", headers=admin.cabeceras)
    assert sin_base.status_code == 409

    await _cargar(cliente, admin, EJEMPLO)  # deja una indexación en cola
    en_curso = await cliente.post("/api/conocimiento/indexacion", headers=admin.cabeceras)
    assert en_curso.status_code == 409

    trabajo = await sesion.scalar(select(TrabajoProcesamiento))
    assert trabajo is not None
    trabajo.estado = EstadoTrabajo.COMPLETADO
    await sesion.commit()
    assert (
        await cliente.post("/api/conocimiento/indexacion", headers=admin.cabeceras)
    ).status_code == 202


# --- Criterio 5: solo un administrador carga o reemplaza la base ------------------------------


async def test_un_revisor_no_puede_cargar_ni_reindexar(
    cliente: httpx.AsyncClient,
    sesion: AsyncSession,
    admin: Identidad,
    publicador: PublicadorFalso,
) -> None:
    revisor = await agregar_miembro(sesion, admin.organizacion_id, Rol.REVISOR)

    carga = await _cargar(cliente, revisor, EJEMPLO)
    reindexacion = await cliente.post("/api/conocimiento/indexacion", headers=revisor.cabeceras)

    assert carga.status_code == reindexacion.status_code == 403
    assert await _fuentes(sesion, admin) == []
    assert publicador.publicados == []
