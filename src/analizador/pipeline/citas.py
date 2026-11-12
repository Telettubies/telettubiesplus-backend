"""Etapa 8: validar que las citas existen en su fuente (RF-05, N-02, P-04, R-05, HU-B18).

Un modelo puede inventar o reformular una cita. Aquí, en código, cada cita de un ítem
cualitativo se compara con el texto del fragmento que el modelo dijo usar, tolerando
diferencias menores (tildes, mayúsculas, espacios, puntuación). Si la similitud queda por debajo
del umbral, la evidencia se marca como no verificada y el ítem pasa a revisión: ninguna cita
no verificada sostiene un «cumple».

Del mismo modo, el texto literal de cada requisito se compara con el de su sección del pliego
y se marca si no aparece, como posible reformulación del modelo (R-17).
"""

import logging
import re
import unicodedata
import uuid
from typing import Any

from rapidfuzz import fuzz
from sqlalchemy import select

from analizador.core.config import get_settings
from analizador.db.session import get_sessionmaker
from analizador.models import Item, Requisito, Resultado, Seccion
from analizador.pipeline import progreso
from analizador.pipeline.estado import EstadoAnalisis

logger = logging.getLogger(__name__)

MOTIVO_CITA_NO_VERIFICADA = "La cita de la evidencia no se encontró en la fuente indicada."
_NO_ALFANUMERICO = re.compile(r"[^0-9a-z]+")


def normalizar(texto: str) -> str:
    """Minúsculas, sin tildes ni puntuación y con espacios simples."""
    sin_tildes = unicodedata.normalize("NFKD", texto)
    sin_tildes = "".join(c for c in sin_tildes if not unicodedata.combining(c))
    return _NO_ALFANUMERICO.sub(" ", sin_tildes.casefold()).strip()


def similitud(cita: str, fuente: str) -> float:
    """Qué tan bien aparece `cita` dentro de `fuente`, de 0 a 100.

    Busca el tramo de la fuente que más se parece a la cita. Una cita más larga que su fuente
    no puede estar contenida en ella: se comparan enteras, para que añadir texto inventado a
    un fragmento real no cuente como coincidencia.
    """
    buscada, texto = normalizar(cita), normalizar(fuente)
    if not buscada or not texto:
        return 0.0
    if buscada in texto:
        return 100.0
    if len(buscada) > len(texto):
        return float(fuzz.ratio(buscada, texto))
    return float(fuzz.partial_ratio(buscada, texto))


def agregar_motivo(actual: str | None, motivo: str) -> str:
    motivos = [m for m in (actual or "").split(" | ") if m]
    if motivo not in motivos:
        motivos.append(motivo)
    return " | ".join(motivos)


def validar_evidencia(
    evidencia: list[dict[str, Any]], umbral: float
) -> tuple[list[dict[str, Any]], bool]:
    """Evidencia con cada cita marcada como verificada o no, y si alguna no lo está.

    Las citas ya comprobadas y los datos estructurados (ya verificados por construcción) no
    se vuelven a tocar.
    """
    validada: list[dict[str, Any]] = []
    for entrada in evidencia:
        if entrada.get("tipo") == "cita" and entrada.get("verificada") is None:
            valor = similitud(str(entrada.get("cita") or ""), str(entrada.get("texto") or ""))
            entrada = {**entrada, "verificada": valor >= umbral, "similitud": round(valor, 1)}
        validada.append(entrada)
    alguna_falla = any(e.get("tipo") == "cita" and e.get("verificada") is False for e in validada)
    return validada, alguna_falla


# --- Etapa del pipeline ---------------------------------------------------------------------


async def ejecutar(estado: EstadoAnalisis) -> None:
    """Valida las citas y los textos literales aún sin comprobar. Repetirla no cambia nada."""
    umbral = get_settings().citas_umbral_similitud
    licitacion_id = uuid.UUID(estado["licitacion_id"])
    async with get_sessionmaker()() as session:
        resultados = list(
            await session.scalars(
                select(Resultado)
                .join(Item, Resultado.item_id == Item.id)
                .join(Requisito, Item.requisito_id == Requisito.id)
                .where(Requisito.licitacion_id == licitacion_id)
            )
        )
        for resultado in resultados:
            pendiente = any(
                e.get("tipo") == "cita" and e.get("verificada") is None for e in resultado.evidencia
            )
            if not pendiente:
                continue
            resultado.evidencia, alguna_falla = validar_evidencia(resultado.evidencia, umbral)
            if alguna_falla:
                resultado.requiere_revision = True
                resultado.motivo_revision = agregar_motivo(
                    resultado.motivo_revision, MOTIVO_CITA_NO_VERIFICADA
                )

        filas = await session.execute(
            select(Requisito, Seccion.texto)
            .join(Seccion, Requisito.seccion_id == Seccion.id)
            .where(Requisito.licitacion_id == licitacion_id)
        )
        requisitos = [(requisito, texto) for requisito, texto in filas]
        for requisito, texto_seccion in requisitos:
            if requisito.texto_verificado is None:
                requisito.texto_verificado = (
                    similitud(requisito.texto_literal, texto_seccion) >= umbral
                )
        await session.commit()

        citas = [e for r in resultados for e in r.evidencia if e.get("tipo") == "cita"]
        cifras = {
            "citas_verificadas": sum(1 for e in citas if e.get("verificada") is True),
            "citas_no_verificadas": sum(1 for e in citas if e.get("verificada") is False),
            "literales_verificados": sum(1 for r, _ in requisitos if r.texto_verificado),
            "literales_no_verificados": sum(
                1 for r, _ in requisitos if r.texto_verificado is False
            ),
        }
    if cifras["citas_no_verificadas"] or cifras["literales_no_verificados"]:
        logger.warning("Licitación %s: validación de citas %s", licitacion_id, cifras)
    await progreso.anotar_detalle(uuid.UUID(estado["trabajo_id"]), validacion=cifras)
