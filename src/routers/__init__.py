# routers/__init__.py

# Exponemos los routers para que se puedan importar de manera agrupada y limpia
from .auth import router as auth_router
from .antennas import router as antennas_router
from .labels import router as labels_router
