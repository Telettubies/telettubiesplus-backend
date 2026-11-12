"""Configuración de logging.

S-14: no registrar tokens, claves ni contenido de hojas de vida. Registrar solo identificadores.
"""

import logging


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # Las librerías HTTP registran URLs y cabeceras en DEBUG; se mantienen en WARNING.
    for ruidoso in ("httpx", "httpx2", "httpcore", "botocore", "aiormq", "aio_pika"):
        logging.getLogger(ruidoso).setLevel(logging.WARNING)
