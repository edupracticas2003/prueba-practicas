# Utiliza la versión oficial ligera de Python basada en Debian
FROM python:3.11-slim

# Evita que Python escriba archivos .pyc en el disco y asegura salida de logs limpia
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Instala dependencias del sistema necesarias para conectores de bases de datos
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libmariadb-dev \
    pkg-config \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Establece el directorio de trabajo dentro del contenedor
WORKDIR /app

# Instala las librerías necesarias para conectarte a MySQL y procesar datos
RUN pip install --no-cache-dir \
    mysql-connector-python \
    pymysql \
    cryptography \
    fastapi \
    uvicorn \
    requests && \
    useradd --create-home appuser

COPY --chown=appuser:appuser src/ /app/

USER appuser

# Al usar un volumen mapeado, no copiamos el código aquí para poder editarlo en vivo.
# El comando por defecto ejecutará tu script automáticamente.
# CMD ["python", "com-labels.py"]
CMD uvicorn api_server:app --host 0.0.0.0 --port 8000 & python com-labels.py
