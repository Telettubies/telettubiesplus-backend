"""Salud del servicio (para el proxy y el despliegue)."""

from typing import Any

from fastapi import APIRouter, Response, status
from sqlalchemy import text

from analizador.api.deps import PublicadorDep, SessionDep

router = APIRouter(tags=["salud"])


@router.get("/health")
async def vivo() -> dict[str, str]:
    return {"estado": "ok"}


@router.get("/health/ready")
async def listo(
    session: SessionDep, publicador: PublicadorDep, response: Response
) -> dict[str, Any]:
    checks: dict[str, bool] = {}
    try:
        await session.execute(text("SELECT 1"))
        checks["postgres"] = True
    except Exception:
        checks["postgres"] = False
    checks["rabbitmq"] = publicador.conectado
    if not all(checks.values()):
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"estado": "ok" if all(checks.values()) else "degradado", "checks": checks}
