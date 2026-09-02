"""Configuracion global de salida para los procesos Python del despliegue."""

import builtins
import os


RENDER_ENVIRONMENT = bool(
    os.environ.get("PORT")
    or os.environ.get("RENDER")
    or os.environ.get("RENDER_SERVICE_ID")
    or os.environ.get("RENDER_EXTERNAL_URL")
)

if RENDER_ENVIRONMENT:
    builtins.print = lambda *args, **kwargs: None
