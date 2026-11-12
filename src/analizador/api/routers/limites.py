"""Límites de uso vigentes y consumo de la organización (S-08, HU-B26).

El frontend los muestra antes de cargar un pliego (HU-F05) y al usar el chat (HU-F12).
"""

from fastapi import APIRouter

from analizador.api import cuotas
from analizador.api.deps import ContextoDep, SessionDep
from analizador.core.config import get_settings
from analizador.schemas import LimitesOut, UsoOut

router = APIRouter(prefix="/api/limites", tags=["límites"])


@router.get("", response_model=LimitesOut)
async def limites(contexto: ContextoDep, session: SessionDep) -> LimitesOut:
    settings = get_settings()
    return LimitesOut(
        max_pdf_mb=settings.max_pdf_mb,
        max_pdf_paginas=settings.max_pdf_paginas,
        chat_max_caracteres=settings.chat_max_caracteres,
        ventana_horas=int(cuotas.VENTANA.total_seconds() // 3600),
        analisis=UsoOut(
            usados=await cuotas.analisis_recientes(session, contexto.organizacion_id),
            cuota=settings.cuota_analisis_diaria,
        ),
        chat=UsoOut(
            usados=await cuotas.preguntas_recientes(session, contexto.organizacion_id),
            cuota=settings.cuota_chat_diaria,
        ),
    )
