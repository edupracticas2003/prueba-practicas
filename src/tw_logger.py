# tw_logger.py
import json
import os
import logging
import sys
import tempfile

from datetime import datetime

# Configuración de rutas
LOG_DIR = os.getenv("COM_LABELS_LOG_DIR", "/var/log/com-labels")
try:
    os.makedirs(LOG_DIR, exist_ok=True)
except OSError:
    LOG_DIR = os.path.join(tempfile.gettempdir(), "com-labels")
    os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, "app-com-labels.log")

class JsonFormatter(logging.Formatter):
    """Formatter personalizado que transforma el registro de log en una línea JSON estructurada."""
    def format(self, record):
        payload = {
            "timestamp":    datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S"),
            "level":        record.levelname,
            "message":      record.getMessage(),
            "logger":       record.name
        }
        
        # Inyectar trazabilidad automática si existe en los argumentos extra
        if hasattr(record, "trace_id"):
            payload["trace_id"] = record.trace_id
            
        # Incluir datos extra que pases en el log
        if hasattr(record, "extra_data"):
            payload["extra"] = record.extra_data
            
        return json.dumps(payload, ensure_ascii=False)

# Configuración del Logger
logger = logging.getLogger("com-labels")
logger.setLevel(logging.INFO)

if not logger.handlers:
    # Handler para el archivo dentro del contenedor
    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    file_handler.setFormatter(JsonFormatter())
    logger.addHandler(file_handler)
    

def log_loki(nivel: str, mensaje: str, extra: dict = None, trace_id: str = None):
    """
    Función de compatibilidad para registrar logs estructurados de forma segura.
    
    :param nivel: 'INFO', 'WARNING', 'ERROR', 'DEBUG'
    :param mensaje: El cuerpo del mensaje de log
    :param extra: Diccionario con metadatos adicionales para filtros en Grafana
    :param trace_id: ID único para trazabilidad distribuida
    """
    level_num = getattr(logging, nivel.upper(), logging.INFO)
    
    # Pasamos las variables a través del diccionario 'extra' nativo de Python logging
    context = {}
    if extra:
        context["extra_data"] = extra
    if trace_id:
        context["trace_id"] = trace_id
        
    logger.log(level_num, mensaje, extra=context)
