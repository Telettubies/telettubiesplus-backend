"""HU-B04: aislar los datos de cada organización (ADR-014, S-03, R-18).

Dos organizaciones, ALFA y BETA, cada una con una fila de matriz completa. Las pruebas
actúan como BETA e intentan alcanzar los datos de ALFA.
"""

import re
import uuid
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from analizador.db.busqueda import fragmentos_cercanos
from analizador.models import (
    Fragmento,
    Licitacion,
    Miembro,
    Resultado,
    Revision,
    TrabajoProcesamiento,
)
from analizador.models.enums import EstadoCumplimiento, EstadoTrabajo, OrigenFragmento, Rol
from tests.apoyo import (
    AlmacenFalso,
    Identidad,
    Matriz,
    PublicadorFalso,
    crear_organizacion,
    sembrar_matriz,
)

PDF = b"%PDF-1.7\n% pliego de prueba\n"

# Cuerpo válido para cada operación de escritura con identificador en la ruta. El barrido
# falla si aparece una operación nueva sin cuerpo de ejemplo: así no queda sin probar.
CUERPOS: dict[tuple[str, str], dict[str, Any] | None] = {
    ("post", "/api/licitaciones/{licitacion_id}/reintentar"): None,
    ("post", "/api/licitaciones/{licitacion_id}/chat"): {"pregunta": "¿Qué exige el pliego?"},
    ("post", "/api/licitaciones/{licitacion_id}/observaciones"): None,
    ("post", "/api/resultados/{resultado_id}/revisiones"): {
        "estado_nuevo": "cumple",
        "comentario": "intento desde otra organización",
    },
    ("patch", "/api/miembros/{miembro_id}/rol"): {"rol": "revisor"},
    ("delete", "/api/miembros/{miembro_id}"): None,
}


@dataclass(frozen=True)
class Escenario:
    alfa: Identidad
    beta: Identidad
    matriz_alfa: Matriz
    matriz_beta: Matriz

    @property
    def ids_de_alfa(self) -> list[uuid.UUID]:
        return [*self.matriz_alfa.ids, self.alfa.miembro_id, self.alfa.organizacion_id]


@pytest.fixture
async def escenario(sesion: AsyncSession) -> Escenario:
    alfa = await crear_organizacion(sesion, "ALFA")
    beta = await crear_organizacion(sesion, "BETA")
    return Escenario(
        alfa=alfa,
        beta=beta,
        matriz_alfa=await sembrar_matriz(sesion, alfa, "ALFA"),
        matriz_beta=await sembrar_matriz(sesion, beta, "BETA"),
    )


def _url(ruta: str, identificador: uuid.UUID) -> str:
    return re.sub(r"\{[^}]+\}", str(identificador), ruta)


# --- Criterio 1: toda consulta filtra por la organización del usuario -----------------------


async def test_los_listados_solo_devuelven_datos_propios(
    cliente: httpx.AsyncClient, escenario: Escenario
) -> None:
    for quien, propia, ajena in (
        (escenario.alfa, "ALFA", "BETA"),
        (escenario.beta, "BETA", "ALFA"),
    ):
        for url in ("/api/licitaciones", "/api/conocimiento/fuentes", "/api/miembros"):
            respuesta = await cliente.get(url, headers=quien.cabeceras)
            assert respuesta.status_code == 200
            assert len(respuesta.json()) == 1, url
            assert ajena not in respuesta.text, f"{url} devuelve datos de {ajena} a {propia}"

    propias = await cliente.get("/api/licitaciones", headers=escenario.beta.cabeceras)
    assert propias.json()[0]["id"] == str(escenario.matriz_beta.licitacion_id)


async def test_el_detalle_propio_trae_la_matriz_completa(
    cliente: httpx.AsyncClient, escenario: Escenario
) -> None:
    """Control del barrido: el dueño sí ve los datos que al otro se le niegan."""
    respuesta = await cliente.get(
        f"/api/licitaciones/{escenario.matriz_alfa.licitacion_id}",
        headers=escenario.alfa.cabeceras,
    )
    assert respuesta.status_code == 200
    assert "Requisito ALFA" in respuesta.text
    assert "Evidencia ALFA" in respuesta.text


# --- Criterio 3: un recurso ajeno responde 404, igual que si no existiera -------------------


async def test_ningun_endpoint_entrega_datos_de_otra_organizacion(
    app: FastAPI, cliente: httpx.AsyncClient, escenario: Escenario
) -> None:
    """Barrido del contrato OpenAPI como BETA con todos los identificadores de ALFA."""
    probadas = 0
    for ruta, operaciones in app.openapi()["paths"].items():
        con_identificador = "{" in ruta
        for metodo in operaciones:
            if metodo != "get" and not con_identificador:
                continue  # altas sin identificador: se prueban en las escrituras
            if metodo != "get":
                assert (metodo, ruta) in CUERPOS, f"Falta un cuerpo de ejemplo para {metodo} {ruta}"
            candidatos = escenario.ids_de_alfa if con_identificador else [uuid.uuid4()]
            for identificador in candidatos:
                respuesta = await cliente.request(
                    metodo.upper(),
                    _url(ruta, identificador),
                    headers=escenario.beta.cabeceras,
                    json=CUERPOS.get((metodo, ruta)),
                )
                descripcion = f"{metodo.upper()} {ruta} con {identificador}"
                assert "ALFA" not in respuesta.text, f"{descripcion} filtra datos de ALFA"
                for ajeno in escenario.ids_de_alfa:
                    assert str(ajeno) not in respuesta.text, f"{descripcion} filtra un id de ALFA"
                if con_identificador:
                    assert respuesta.status_code == 404, descripcion
                    inexistente = await cliente.request(
                        metodo.upper(),
                        _url(ruta, uuid.uuid4()),
                        headers=escenario.beta.cabeceras,
                        json=CUERPOS.get((metodo, ruta)),
                    )
                    assert inexistente.status_code == 404
                    assert inexistente.json() == respuesta.json(), (
                        f"{descripcion} se distingue de un recurso inexistente"
                    )
                probadas += 1
    assert probadas > 20, "el barrido debería cubrir todas las rutas con identificador"


# --- Integridad: ninguna escritura afecta ni se asocia a otra organización ------------------


async def test_los_intentos_de_escritura_no_modifican_datos_ajenos(
    cliente: httpx.AsyncClient,
    sesion: AsyncSession,
    escenario: Escenario,
    publicador: PublicadorFalso,
) -> None:
    ajena, beta = escenario.matriz_alfa, escenario.beta.cabeceras
    intentos = [
        (
            "POST",
            f"/api/resultados/{ajena.resultado_id}/revisiones",
            CUERPOS[("post", "/api/resultados/{resultado_id}/revisiones")],
        ),
        ("POST", f"/api/licitaciones/{ajena.licitacion_id}/reintentar", None),
        ("PATCH", f"/api/miembros/{escenario.alfa.miembro_id}/rol", {"rol": "revisor"}),
        ("DELETE", f"/api/miembros/{escenario.alfa.miembro_id}", None),
    ]
    for metodo, url, cuerpo in intentos:
        respuesta = await cliente.request(metodo, url, headers=beta, json=cuerpo)
        assert respuesta.status_code == 404, f"{metodo} {url}"

    assert await sesion.scalar(select(func.count()).select_from(Revision)) == 0
    resultado = await sesion.get(Resultado, ajena.resultado_id)
    assert resultado is not None and resultado.estado == EstadoCumplimiento.NO_CUMPLE
    trabajo = await sesion.get(TrabajoProcesamiento, ajena.trabajo_id)
    assert trabajo is not None and trabajo.estado == EstadoTrabajo.FALLIDO
    miembro = await sesion.get(Miembro, escenario.alfa.miembro_id)
    assert miembro is not None and miembro.rol == Rol.ADMINISTRADOR
    assert publicador.publicados == []


async def test_una_carga_se_asocia_siempre_a_la_organizacion_del_usuario(
    cliente: httpx.AsyncClient, sesion: AsyncSession, escenario: Escenario
) -> None:
    respuesta = await cliente.post(
        "/api/licitaciones",
        headers=escenario.beta.cabeceras,
        files={"archivo": ("pliego.pdf", PDF, "application/pdf")},
        data={"titulo": "Carga", "organizacion_id": str(escenario.alfa.organizacion_id)},
    )
    assert respuesta.status_code == 202

    creada = await sesion.get(Licitacion, uuid.UUID(respuesta.json()["licitacion"]["id"]))
    assert creada is not None
    assert creada.organizacion_id == escenario.beta.organizacion_id
    trabajo = await sesion.get(TrabajoProcesamiento, uuid.UUID(respuesta.json()["trabajo"]["id"]))
    assert trabajo is not None and trabajo.organizacion_id == escenario.beta.organizacion_id


# --- Criterios 4 y 5: almacén de objetos ----------------------------------------------------


async def test_la_url_temporal_solo_se_firma_para_archivos_propios(
    cliente: httpx.AsyncClient, escenario: Escenario, almacen: AlmacenFalso
) -> None:
    ajeno = await cliente.get(
        f"/api/licitaciones/{escenario.matriz_alfa.licitacion_id}",
        headers=escenario.beta.cabeceras,
    )
    assert ajeno.status_code == 404
    assert almacen.firmadas == []

    propio = await cliente.get(
        f"/api/licitaciones/{escenario.matriz_alfa.licitacion_id}",
        headers=escenario.alfa.cabeceras,
    )
    assert propio.status_code == 200
    assert almacen.firmadas == [escenario.matriz_alfa.storage_key]
    assert escenario.matriz_alfa.storage_key in propio.json()["url_pdf"]


async def test_no_se_firma_una_clave_fuera_del_prefijo_de_la_organizacion(
    cliente: httpx.AsyncClient, sesion: AsyncSession, escenario: Escenario, almacen: AlmacenFalso
) -> None:
    """Aunque un dato corrupto apunte al archivo de otra organización, no se entrega."""
    propia = await sesion.get(Licitacion, escenario.matriz_beta.licitacion_id)
    assert propia is not None
    propia.storage_key = escenario.matriz_alfa.storage_key
    await sesion.commit()

    respuesta = await cliente.get(
        f"/api/licitaciones/{escenario.matriz_beta.licitacion_id}",
        headers=escenario.beta.cabeceras,
    )
    assert respuesta.status_code == 404
    assert almacen.firmadas == []


async def test_los_archivos_se_guardan_bajo_el_prefijo_de_la_organizacion_con_nombre_aleatorio(
    cliente: httpx.AsyncClient, escenario: Escenario, almacen: AlmacenFalso
) -> None:
    for variante in (b"% uno\n", b"% dos\n"):  # contenidos distintos: no son duplicados
        respuesta = await cliente.post(
            "/api/licitaciones",
            headers=escenario.beta.cabeceras,
            files={"archivo": ("Pliego Confidencial 2026.pdf", PDF + variante, "application/pdf")},
        )
        assert respuesta.status_code == 202

    claves = list(almacen.objetos)
    assert len(set(claves)) == 2
    for clave in claves:
        assert clave.startswith(f"organizaciones/{escenario.beta.organizacion_id}/pliegos/")
        assert "Confidencial" not in clave
        assert uuid.UUID(clave.rsplit("/", 1)[1].removesuffix(".pdf"))


# --- Criterio 2: las búsquedas vectoriales filtran por organización y licitación ------------


async def test_la_busqueda_de_evidencia_no_devuelve_fragmentos_de_otra_organizacion(
    sesion: AsyncSession, escenario: Escenario
) -> None:
    # El fragmento de ALFA es idéntico al vector consultado: sería el primero sin el filtro.
    encontrados = await fragmentos_cercanos(
        sesion,
        organizacion_id=escenario.beta.organizacion_id,
        embedding=[1.0, 0.0, 0.0],
        origen=OrigenFragmento.EMPRESA,
        limite=10,
    )
    assert [fragmento.id for fragmento, _ in encontrados] == [escenario.matriz_beta.fragmento_id]
    assert encontrados[0][1] == pytest.approx(0.0)


async def test_la_busqueda_del_chat_filtra_tambien_por_licitacion(
    sesion: AsyncSession, escenario: Escenario
) -> None:
    beta = escenario.beta.organizacion_id
    otra = Licitacion(
        organizacion_id=beta,
        creado_por=escenario.beta.usuario_id,
        titulo="Otra licitación de BETA",
        nombre_archivo="otra.pdf",
        storage_key=f"organizaciones/{beta}/pliegos/{uuid.uuid4()}.pdf",
    )
    sesion.add(otra)
    await sesion.flush()
    fragmentos = {
        "propio": Fragmento(
            organizacion_id=beta,
            origen=OrigenFragmento.PLIEGO,
            licitacion_id=escenario.matriz_beta.licitacion_id,
            texto="pliego consultado",
            embedding=[0.0, 1.0, 0.0],
        ),
        "otra_licitacion": Fragmento(
            organizacion_id=beta,
            origen=OrigenFragmento.PLIEGO,
            licitacion_id=otra.id,
            texto="otro pliego de la misma organización",
            embedding=[0.0, 1.0, 0.0],
        ),
        "otra_organizacion": Fragmento(
            organizacion_id=escenario.alfa.organizacion_id,
            origen=OrigenFragmento.PLIEGO,
            licitacion_id=escenario.matriz_alfa.licitacion_id,
            texto="pliego de ALFA",
            embedding=[0.0, 1.0, 0.0],
        ),
    }
    sesion.add_all(fragmentos.values())
    await sesion.commit()

    encontrados = await fragmentos_cercanos(
        sesion,
        organizacion_id=beta,
        embedding=[0.0, 1.0, 0.0],
        origen=OrigenFragmento.PLIEGO,
        limite=10,
        licitacion_id=escenario.matriz_beta.licitacion_id,
    )
    assert [fragmento.id for fragmento, _ in encontrados] == [fragmentos["propio"].id]

    # Pedir la licitación de ALFA con la organización de BETA no devuelve nada.
    cruzada = await fragmentos_cercanos(
        sesion,
        organizacion_id=beta,
        embedding=[0.0, 1.0, 0.0],
        origen=OrigenFragmento.PLIEGO,
        limite=10,
        licitacion_id=escenario.matriz_alfa.licitacion_id,
    )
    assert cruzada == []


async def test_la_busqueda_sobre_el_pliego_exige_la_licitacion(
    sesion: AsyncSession, escenario: Escenario
) -> None:
    with pytest.raises(ValueError, match="licitación"):
        await fragmentos_cercanos(
            sesion,
            organizacion_id=escenario.beta.organizacion_id,
            embedding=[0.0, 1.0, 0.0],
            origen=OrigenFragmento.PLIEGO,
            limite=5,
        )
