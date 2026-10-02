# services/auth_service.py
import secrets
from datetime import datetime, timedelta
from database import MySQLConnection
from tw_logger import log_loki

class AuthService:
    def __init__(self, trace_id: str = "auth-internal"):
        self.trace_id = trace_id

    def login_y_generar_token(self, username: str, password: str) -> dict:
        """Valida credenciales contra la BD usando ejecutar_consulta y devuelve el token activo de 24h o genera uno nuevo."""
        try:
            with MySQLConnection(suffix="_COM", trace_id=self.trace_id) as db:
                
                # 🔍 1. Validamos credenciales consumiendo tu método nativo de lectura (SELECT)
                query_auth = """
                    SELECT user_name, token, expired_at FROM tw_com_labels.api_tokens 
                    WHERE user_name = %s AND password = %s
                """
                resultados = db.ejecutar_consulta(query_auth, (username, password))

                # Si la lista vuelve vacía, las credenciales no existen en MySQL
                if not resultados or len(resultados) == 0:
                    log_loki("WARNING", f"Intento de login fallido para el usuario: {username}", trace_id=self.trace_id)
                    return {"status": "ERROR", "message": "Credenciales inválidas"}

                # Tu método garantiza que cada registro ya es un diccionario puro
                registro = resultados[0]
                token_existente = registro.get("token")
                expired_at = registro.get("expired_at")

                ahora = datetime.now()

                # 🎯 2. COMPROBACIÓN DE PERSISTENCIA (Validamos si el token sigue vigente)
                if token_existente and expired_at:
                    # Control de seguridad por si el conector devolviese la fecha como string en lugar de datetime
                    if isinstance(expired_at, str):
                        try:
                            expired_at = datetime.strptime(expired_at, "%Y-%m-%d %H:%M:%S")
                        except ValueError:
                            expired_at = datetime.strptime(expired_at.split(".")[0], "%Y-%m-%d %H:%M:%S")

                    if expired_at > ahora:
                        log_loki("INFO", f"Reutilizando token válido existente para el usuario: {username}.", trace_id=self.trace_id)
                        return {
                            "status": "SUCCESS",
                            "token": token_existente,
                            "expired_at": expired_at.strftime("%Y-%m-%d %H:%M:%S")
                        }

                # 🔑 3. Si no hay token o caducó, calculamos los nuevos parámetros de 24 horas
                log_loki("INFO", f"Generando nuevo token de 24h para el usuario: {username}.", trace_id=self.trace_id)
                nuevo_token = secrets.token_hex(32)
                nueva_expiracion = ahora + timedelta(hours=24)
                str_expiracion = nueva_expiracion.strftime("%Y-%m-%d %H:%M:%S")

                # 🔄 4. Actualizamos el registro en MySQL invocando tu método de escritura (UPDATE)
                # Tu código de database.py detectará el "UPDATE" y hará el commit() en disco automáticamente
                query_update = """
                    UPDATE tw_com_labels.api_tokens 
                    SET token = %s, expired_at = %s 
                    WHERE user_name = %s
                """
                db.ejecutar_consulta(query_update, (nuevo_token, str_expiracion, username))
                
            return {
                "status": "SUCCESS",
                "token": nuevo_token,
                "expired_at": str_expiracion
            }
            
        except Exception as e:
            log_loki("CRITICAL", f"Error de base de datos en login_y_generar_token: {e}", trace_id=self.trace_id)
            return {"status": "ERROR", "message": f"Error interno: {str(e)}"}

    def validar_token(self, token: str) -> bool:
        """Busca el token en la base de datos utilizando ejecutar_consulta y confirma que no haya expirado."""
        if not token:
            return False

        try:
            with MySQLConnection(suffix="_COM", trace_id=self.trace_id) as db:
                query = """
                    SELECT expired_at FROM tw_com_labels.api_tokens 
                    WHERE token = %s AND expired_at > NOW()
                """
                resultados = db.ejecutar_consulta(query, (token,))
                
                if resultados and len(resultados) > 0:
                    return True
                    
            log_loki("WARNING", "El token proporcionado no existe o ya ha expirado.", trace_id=self.trace_id)
            return False
        except Exception as e:
            log_loki("ERROR", f"Error crítico al validar token en la BD: {e}", trace_id=self.trace_id)
            return False
