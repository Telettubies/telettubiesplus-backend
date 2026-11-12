"""Contrato de los mensajes de la cola.

ADR-015: los mensajes llevan solo identificadores; el estado vive en PostgreSQL.
"""

import uuid

from pydantic import BaseModel


class MensajeTrabajo(BaseModel):
    trabajo_id: uuid.UUID
