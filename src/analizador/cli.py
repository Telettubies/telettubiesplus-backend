"""Comandos de administración.

`crear-organizacion` da de alta una organización con su primer administrador. El DAS no
define todavía cómo se crea una organización (solo cómo un administrador invita revisores),
así que por ahora es una operación manual del equipo.
"""

import argparse
import asyncio
import uuid

from analizador.db.session import get_engine, get_sessionmaker
from analizador.models import Miembro, Organizacion
from analizador.models.enums import Rol


async def _crear_organizacion(nombre: str, admin_usuario_id: uuid.UUID, admin_email: str) -> None:
    async with get_sessionmaker()() as session:
        organizacion = Organizacion(nombre=nombre)
        session.add(organizacion)
        await session.flush()
        session.add(
            Miembro(
                organizacion_id=organizacion.id,
                usuario_id=admin_usuario_id,
                email=admin_email,
                rol=Rol.ADMINISTRADOR,
            )
        )
        await session.commit()
        print(f"Organización creada: {organizacion.id}")
    await get_engine().dispose()


def main() -> None:
    parser = argparse.ArgumentParser(prog="analizador-cli")
    sub = parser.add_subparsers(dest="comando", required=True)

    crear = sub.add_parser("crear-organizacion", help="Crea una organización y su administrador")
    crear.add_argument("--nombre", required=True)
    crear.add_argument(
        "--admin-usuario-id", required=True, type=uuid.UUID, help="id del usuario en Supabase"
    )
    crear.add_argument("--admin-email", required=True)

    args = parser.parse_args()
    if args.comando == "crear-organizacion":
        asyncio.run(_crear_organizacion(args.nombre, args.admin_usuario_id, args.admin_email))


if __name__ == "__main__":
    main()
