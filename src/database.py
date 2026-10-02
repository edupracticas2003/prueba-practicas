# database.py
import os
import time
import mysql.connector

from mysql.connector import Error
from tw_logger import log_loki 

class MySQLConnection:
    """Clase encargada de gestionar de forma segura la conexión y consultas a MySQL con telemetría para Loki."""
    
    def __init__(self, suffix='_COM', db_name=None, trace_id=None):
        # Almacenamos el trace_id para usarlo en todos los logs de esta conexión
        self.trace_id = trace_id
        
        # Formateamos el sufijo del entorno de manera limpia
        formato_sufijo = f"_{suffix.lstrip('_')}" if suffix else ""
        database_entorno = os.getenv(f'DB_NAME{formato_sufijo}', '')

        # Lógica de resolución del nombre de la base de datos
        if db_name is not None and '%' in database_entorno:
            self.database = database_entorno.replace('%', str(db_name))
        elif db_name is not None:
            self.database = db_name
        else:
            self.database = database_entorno

        self.host = os.getenv(f'DB_HOST{formato_sufijo}', 'host.docker.internal')
        self.user = os.getenv(f'DB_USER{formato_sufijo}', 'prueba')
        self.password = os.getenv(f'DB_PASSWORD{formato_sufijo}', 'no_sirve')
        self.port = int(os.getenv(f'DB_PORT{formato_sufijo}', 9999))
        
        self.conexion = None
        self.cursor = None

        # Log estructurado de inicialización (Útil para debugear configuraciones en Grafana)
        log_loki(
            nivel="DEBUG", 
            mensaje=f"Inicializando conexión MySQL para la base de datos: {self.database}",
            extra={"host": self.host, "port": self.port},
            trace_id=self.trace_id
        )

    def __enter__(self):
        """Abre la conexión de forma automática al usar 'with'."""
        try:
            # 1. Agrupamos los parámetros en un diccionario limpio
            conn_args = {
                "host": self.host,
                "user": self.user,
                "password": self.password,
                "port": self.port,
                "connection_timeout": 10,
                "auth_plugin": 'caching_sha2_password',
                # CLI keeps this connection for the whole prompt. Default REPEATABLE READ
                # would freeze the first SELECT snapshot until exit/reconnect.
                "autocommit": True,
            }

            # 2. Inyectamos la base de datos únicamente si se ha definido una
            if hasattr(self, 'database') and self.database:
                conn_args["database"] = self.database
                
            # 3. Hacemos una única llamada pasándole los parámetros estructurados
            self.conexion = mysql.connector.connect(**conn_args)
            self.conexion.autocommit = True
            self.cursor = self.conexion.cursor(dictionary=True)
            return self
        except Error as e:
            log_loki(
                nivel="ERROR", 
                mensaje=f"Fallo crítico: No se pudo conectar al servidor MySQL: {e}", 
                extra={"host": self.host, "database": getattr(self, 'database', '')},
                trace_id=self.trace_id
            )
            raise e

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Cierra el cursor y la conexión automáticamente al salir del bloque 'with'."""
        if self.cursor:
            self.cursor.close()
        if self.conexion and self.conexion.is_connected():
            self.conexion.close()

    def _ensure_fresh_session(self):
        """Evita lecturas stale en el prompt Docker (misma conexión, snapshot REPEATABLE READ)."""
        if not self.conexion:
            return
        try:
            if hasattr(self.conexion, "ping"):
                self.conexion.ping(reconnect=True, attempts=1, delay=0)
        except Error:
            pass
        try:
            self.conexion.autocommit = True
        except Error:
            pass
        try:
            # Cierra el snapshot InnoDB de la conexión larga del prompt, con o sin autocommit.
            self.conexion.rollback()
        except Error:
            pass
        try:
            if self.cursor:
                self.cursor.close()
        except Error:
            pass
        try:
            if self.conexion.is_connected():
                self.cursor = self.conexion.cursor(dictionary=True)
        except Error:
            pass

    def ejecutar_consulta(self, query: str, params=None):
        """Ejecuta una consulta SQL, mide su rendimiento y registra trazas en Loki."""
        self._ensure_fresh_session()
        if not self.cursor:
            return []
            
        query_limpia = query.strip()
        query_tipo = query_limpia.split()[0].upper() if query_limpia else "UNKNOWN"
        
        # Telemetría: Medimos el tiempo que tarda la base de datos en responder
        start_time = time.perf_counter()
        
        try:
            self.cursor.execute(query, params or ())
            duration = round((time.perf_counter() - start_time) * 1000, 2)  # Tiempo en milisegundos (ms)
            
            # 1. Escritura (INSERT)
            if query_tipo == "INSERT":
                self.conexion.commit()
                last_id = self.cursor.lastrowid
                log_loki(
                    nivel="INFO", 
                    mensaje=f"SQL INSERT completado con éxito", 
                    extra={"duration_ms": duration, "inserted_id": last_id},
                    trace_id=self.trace_id
                )
                return last_id
                
            # 2. Modificación (UPDATE / DELETE)
            elif query_tipo in ("UPDATE", "DELETE"):
                self.conexion.commit()
                log_loki(
                    nivel="INFO", 
                    mensaje=f"SQL {query_tipo} completado con éxito", 
                    extra={"duration_ms": duration, "affected_rows": self.cursor.rowcount},
                    trace_id=self.trace_id
                )
                return []
                
            # 3. Lectura (SELECT y otros)
            else:
                resultados = self.cursor.fetchall()
                log_loki(
                    nivel="DEBUG", 
                    mensaje=f"SQL SELECT ejecutado", 
                    extra={"duration_ms": duration, "rows_returned": len(resultados)},
                    trace_id=self.trace_id
                )
                return resultados
            
        except Error as e:
            duration = round((time.perf_counter() - start_time) * 1000, 2)
            log_loki(
                nivel="ERROR", 
                mensaje=f"Error ejecutando consulta SQL: {e}", 
                extra={
                    "query_snippet": query_limpia[:150],  # Guardamos solo el inicio de la query para no saturar
                    "duration_ms": duration
                },
                trace_id=self.trace_id
            )
            raise e
