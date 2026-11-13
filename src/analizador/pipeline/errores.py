"""Errores del pipeline de análisis."""


class AnalisisError(Exception):
    """Fallo previsto cuyo mensaje se puede mostrar al usuario tal cual.

    Úsalo cuando el usuario pueda hacer algo al respecto (por ejemplo, el pliego supera el
    máximo de páginas). El mensaje no debe contener rutas, claves ni texto de proveedores.
    Cualquier otra excepción produce un mensaje genérico con el nombre de la etapa (S-14).
    """

    def __init__(self, mensaje_usuario: str) -> None:
        super().__init__(mensaje_usuario)
        self.mensaje_usuario = mensaje_usuario
