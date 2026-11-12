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
from analizador.models import Item, Licitacion, Requisito, Resultado, Seccion
from analizador.pipeline import progreso
from analizador.pipeline.estado import EstadoAnalisis

logger = logging.getLogger(__name__)

MOTIVO_CITA_NO_VERIFICADA = "La cita de la evidencia no se encontró en la fuente indicada."
MOTIVO_AMBIGUO = "El requisito es ambiguo."
MOTIVO_CONFIANZA_BAJA = "La confianza de la evaluación está por debajo del umbral de revisión."
MOTIVO_REVISION_PENDIENTE = "El ítem está pendiente de revisión."
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
    motivos = (parte.strip() for texto in (actual or "", motivo) for parte in texto.split(" | "))
    return " | ".join(dict.fromkeys(parte for parte in motivos if parte))


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
    """Valida citas y textos literales y registra motivos de revisión (HU-B18, HU-B20)."""
    settings = get_settings()
    umbral = settings.citas_umbral_similitud
    licitacion_id = uuid.UUID(estado["licitacion_id"])
    organizacion_id = uuid.UUID(estado["organizacion_id"])
    async with get_sessionmaker()() as session:
        filas_resultados = (
            await session.execute(
                select(Resultado, Requisito, Resultado.revisiones.any())
                .select_from(Resultado)
                .join(Item, Resultado.item_id == Item.id)
                .join(Requisito, Item.requisito_id == Requisito.id)
                .join(Licitacion, Requisito.licitacion_id == Licitacion.id)
                .where(
                    Licitacion.id == licitacion_id, Licitacion.organizacion_id == organizacion_id
                )
            )
        ).all()
        for resultado, requisito, revisado in filas_resultados:
            evidencia, alguna_falla = validar_evidencia(resultado.evidencia, umbral)
            if evidencia != resultado.evidencia:
                resultado.evidencia = evidencia
            if not revisado:
                _marcar_revision(
                    resultado, requisito, alguna_falla, settings.revision_umbral_confianza
                )

        filas = await session.execute(
            select(Requisito, Seccion.texto)
            .select_from(Requisito)
            .join(Seccion, Requisito.seccion_id == Seccion.id)
            .join(Licitacion, Requisito.licitacion_id == Licitacion.id)
            .where(Licitacion.id == licitacion_id, Licitacion.organizacion_id == organizacion_id)
        )
        requisitos = [(requisito, texto) for requisito, texto in filas]
        for requisito, texto_seccion in requisitos:
            if requisito.texto_verificado is None:
                requisito.texto_verificado = (
                    similitud(requisito.texto_literal, texto_seccion) >= umbral
                )
        await session.commit()

        citas = [
            e
            for resultado, _, _ in filas_resultados
            for e in resultado.evidencia
            if e.get("tipo") == "cita"
        ]
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


def _marcar_revision(
    resultado: Resultado, requisito: Requisito, cita_no_verificada: bool, umbral: float
) -> None:
    motivos = resultado.motivo_revision
    if requisito.ambiguo:
        motivos = agregar_motivo(motivos, requisito.motivo_ambiguedad or MOTIVO_AMBIGUO)
    if cita_no_verificada:
        motivos = agregar_motivo(motivos, MOTIVO_CITA_NO_VERIFICADA)
    if resultado.confianza is not None and resultado.confianza < umbral:
        motivos = agregar_motivo(motivos, MOTIVO_CONFIANZA_BAJA)
    if motivos or resultado.requiere_revision:
        resultado.requiere_revision = True
        resultado.motivo_revision = motivos or MOTIVO_REVISION_PENDIENTE
