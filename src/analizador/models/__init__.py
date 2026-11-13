"""Modelos ORM. Importarlos aquí registra todas las tablas en Base.metadata (Alembic)."""

from analizador.models.conocimiento import EmbeddingCache, Fragmento, FuenteConocimiento
from analizador.models.documento import CambioConocimiento, DocumentoEmpresa
from analizador.models.licitacion import (
    Item,
    Licitacion,
    PaginaPliego,
    Requisito,
    Resultado,
    Revision,
    Seccion,
    UbicacionPliego,
)
from analizador.models.organizacion import AuditoriaMiembro, Miembro, Organizacion
from analizador.models.trabajo import TrabajoProcesamiento
from analizador.models.uso import EventoUso

__all__ = [
    "AuditoriaMiembro",
    "CambioConocimiento",
    "DocumentoEmpresa",
    "EmbeddingCache",
    "EventoUso",
    "Fragmento",
    "FuenteConocimiento",
    "Item",
    "Licitacion",
    "Miembro",
    "Organizacion",
    "PaginaPliego",
    "Requisito",
    "Resultado",
    "Revision",
    "Seccion",
    "TrabajoProcesamiento",
    "UbicacionPliego",
]
