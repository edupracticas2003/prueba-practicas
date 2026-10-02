# services/solum_core.py
import os
import time
import requests
from typing import Dict, Any, Optional
from tw_logger import log_loki

class SolumAPIError(Exception):
    """Excepción base para fallos controlados de comunicación."""
    pass


_TIMEOUT_MARKERS = (
    "CONNECTTIMEOUT",
    "CONNECT TIMEOUT",
    "READTIMEOUT",
    "READ TIMEOUT",
    "TIMED OUT",
    "TIMEOUTERROR",
)


def is_solum_timeout(err: Any) -> bool:
    """True si el fallo es connect/read timeout de Solum (no otros API errors)."""
    seen = set()
    current = err
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if "TIMEOUT" in type(current).__name__.upper():
            return True
        text = str(current).upper()
        if any(marker in text for marker in _TIMEOUT_MARKERS):
            return True
        current = getattr(current, "__cause__", None) or getattr(current, "__context__", None)
    return False


_SOLUM_TRACE_HEADER_KEYS = (
    "trace_id",
    "x-trace-id",
    "x-request-id",
    "traceid",
    "x-amzn-trace-id",
    "x-correlation-id",
)

# AIMS SaaS: detail/type/info suelen devolver responseCode/responseMessage ("OK")
# sin trace_id. Los ids de petición Solum aparecen como batch/tx en otros endpoints.
_SOLUM_BODY_ID_KEYS = (
    "trace_id",
    "traceId",
    "trace-id",
    "customBatchId",
    "inputBatchId",
    "batchId",
    "txSequence",
)


def _coerce_id(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    if isinstance(value, (dict, list, bool)):
        return None
    text = str(value).strip()
    if not text or text.upper() in ("OK", "SUCCESS", "ERROR", "FAIL", "FAILED", "NULL", "NONE"):
        # responseMessage de AIMS es "OK"/"SUCCESS" — no es un id.
        return None
    return text[:64]


def _find_solum_id_in_obj(obj: Any, depth: int = 0) -> Optional[str]:
    if obj is None or depth > 5:
        return None
    if isinstance(obj, dict):
        for key in _SOLUM_BODY_ID_KEYS:
            found = _coerce_id(obj.get(key))
            if found:
                return found
        # latestBatchInfo.txSequence (labels/detail)
        nested_batch = obj.get("latestBatchInfo")
        if isinstance(nested_batch, dict):
            found = _coerce_id(nested_batch.get("txSequence")) or _coerce_id(
                nested_batch.get("inputBatchId")
            )
            if found:
                return found
        for nested_key in ("responseMessage", "data", "result", "timelineInfo"):
            nested = obj.get(nested_key)
            found = _find_solum_id_in_obj(nested, depth + 1)
            if found:
                return found
        # Recorrido amplio por si el id va en otra rama.
        for value in obj.values():
            if isinstance(value, (dict, list)):
                found = _find_solum_id_in_obj(value, depth + 1)
                if found:
                    return found
    elif isinstance(obj, list):
        for item in obj:
            found = _find_solum_id_in_obj(item, depth + 1)
            if found:
                return found
    return None


def extract_solum_trace_id(response: Any) -> Optional[str]:
    """
    Extrae un id de petición Solum desde header/body AIMS.

    Paths reales vistos en guía/fixtures:
    - headers: X-Trace-Id / X-Request-Id (si el gateway los envía)
    - body: customBatchId / inputBatchId / txSequence / latestBatchInfo.txSequence
    - NOTA: gateway/detail y labels/detail típicos solo traen
      responseCode + responseMessage=\"OK\" — sin trace_id.
    """
    if response is None:
        return None
    headers = getattr(response, "headers", None) or {}
    try:
        items = headers.items()
    except Exception:
        items = []
    header_map = {str(key).lower(): value for key, value in items}
    for key in _SOLUM_TRACE_HEADER_KEYS:
        found = _coerce_id(header_map.get(key))
        if found:
            return found

    text = getattr(response, "text", None) or ""
    if not text:
        return None
    try:
        data = response.json()
    except Exception:
        return None
    return _find_solum_id_in_obj(data)


def resolve_solum_trace_id_for_log(
    response: Any,
    *,
    correlation_id: Optional[str] = None,
    contacted: bool = False,
) -> Optional[str]:
    """
    Id a persistir en trace_log.trace_id_solum.

    - Éxito (hay respuesta HTTP de Solum): id extraído del response, o si AIMS
      no trae ninguno (caso típico detail), el correlation id del flujo Solum
      (`cli-*` / `web-api-*`) que ya usa SolumCoreClient.
    - Fallo/timeout (sin respuesta): NULL.
    """
    if not contacted:
        return None
    extracted = extract_solum_trace_id(response)
    if extracted:
        return extracted
    cid = str(correlation_id or "").strip()
    if cid.startswith(("cli-", "web-api-", "web-login-")):
        return cid[:64]
    return None

class SolumCoreClient:
    """
    Cliente Core de Transporte.
    Maneja EXCLUSIVAMENTE el ciclo de vida del token de 24h por compañía, llamadas HTTP y generación de cURL para DEBUG.
    """
    # 🎯 EVOLUCIÓN IMPRESCINDIBLE: Diccionarios dinámicos para almacenar los tokens y expiraciones de TWI y 962
    _tokens: Dict[str, Optional[str]] = {"TWI": None, "962": None}
    _tokens_expires_at: Dict[str, float] = {"TWI": 0.0, "962": 0.0}

    def __init__(self, trace_id: str = "internal"):
        self.trace_id = trace_id
        # Forzamos que se mantenga la base inmutable eliminando slashes sobrantes
        self.base_url = os.getenv("SOLUM_API_URL", "https://solumesl.com").rstrip('/')
        
        # Validación de la URL base
        if not self.base_url:
            error_env = "CRITICAL: Missing Solum environment variable SOLUM_API_URL"
            log_loki("CRITICAL", error_env, trace_id=self.trace_id)
            raise RuntimeError(error_env)

    def _login(self, company: str) -> str:
        """Realiza el inicio de sesión v2 seleccionando las credenciales del .env según la compañía."""
        log_loki("INFO", f"Attempting login against Solum AIMS API v2 for company {company}...", trace_id=self.trace_id)
        
        # 🎯 Selección dinámica y quirúrgica de las credenciales del .env
        comp_key = "TWI" if str(company).strip().upper() == "TWI" else "962"
        user = os.getenv(f"SOLUM_USER_{comp_key}")
        password = os.getenv(f"SOLUM_PASSWORD_{comp_key}")

        if not user or not password:
            error_msg = f"CRITICAL: Missing environment variables for company {comp_key}"
            log_loki("CRITICAL", error_msg, trace_id=self.trace_id)
            raise RuntimeError(error_msg)

        login_url = f"{self.base_url}/token"
        payload = {"username": user, "password": password}

        try:
            response = requests.post(login_url, json=payload, timeout=8)
            response.raise_for_status()
            data = response.json()

            response_message = data.get("responseMessage") or {}
            token = response_message.get("access_token") if isinstance(response_message, dict) else None

            if not token:
                raise SolumAPIError(f"Login v2 succeeded but 'access_token' was not found for {comp_key}: {data}")

            # Almacenamos el token de forma aislada para la compañía en curso
            SolumCoreClient._tokens[comp_key] = token
            SolumCoreClient._tokens_expires_at[comp_key] = time.time() + (24 * 60 * 60) - 300
            log_loki("INFO", f"Solum API v2 authenticated and token cached for {comp_key}.", trace_id=self.trace_id)
            return token

        except requests.exceptions.RequestException as e:
            error_msg = f"Authentication failed against Solum API (v2 token) for {comp_key}: {str(e)}"
            log_loki("CRITICAL", error_msg, trace_id=self.trace_id)
            raise SolumAPIError(error_msg) from e

    def _ensure_valid_token(self, company: str) -> str:
        """Asegura la validez del token discriminando por el scope de la compañía."""
        comp_key = "TWI" if str(company).strip().upper() == "TWI" else "962"
        
        if not SolumCoreClient._tokens[comp_key] or time.time() >= SolumCoreClient._tokens_expires_at[comp_key]:
            return self._login(comp_key)
        return SolumCoreClient._tokens[comp_key]

    def _log_curl_debug(self, method: str, url: str, headers: dict, json_data: Optional[dict], params: Optional[dict]) -> None:
        """Construye y registra un comando cURL equivalente para depuración."""
        req = requests.models.Request(method, url, params=params).prepare()
        full_url = req.url

        curl_parts = [f"curl -X {method} '{full_url}'"]
        
        for key, value in headers.items():
            curl_parts.append(f"-H '{key}: {value}'")
            
        if json_data:
            import json
            compact_json = json.dumps(json_data, ensure_ascii=False)
            curl_parts.append(f"-d '{compact_json}'")
            
        curl_command = " \\\n  ".join(curl_parts)
        
        # Guardamos en los logs centralizados de Loki como INFO
        log_loki("INFO", f"Generated Solum API cURL:\n{curl_command}", trace_id=self.trace_id)
        
        # 🎯 FORZADO EXPERTO: Si DEBUG es true, lo escupe en directo por pantalla
        if os.getenv("DEBUG", "").lower() in ("true", "1"):
            print(f"\n⚙️  [DEBUG cURL] Sending request:\n{curl_command}\n")

    def request(self, method: str, endpoint: str, json_data: Optional[Dict] = None, params: Optional[Dict] = None, company: str = "962") -> Any:
        """Método de transporte genérico con logger automático de cURL y discriminación por compañía."""
        
        # 🔐 VALIDACIÓN DE SEGURIDAD POR TOKEN DE 24 HORAS (EXCLUSIVA PARA PETICIONES NO-CLI)
        is_cli_call = isinstance(self.trace_id, str) and self.trace_id.startswith("cli-")
        
        if not is_cli_call:
            # Importación local para prevenir importaciones cíclicas
            from services.auth_service import AuthService
            auth = AuthService(trace_id=self.trace_id)
            
            # Recuperamos el token inyectado temporalmente en el entorno por tu servidor de API
            token_entrante = os.getenv("INCOMING_API_TOKEN")
            
            if not token_entrante or not auth.validar_token(token_entrante):
                error_auth_msg = "SECURITY ERROR: Acceso denegado. Token inválido, ausente o expirado."
                log_loki("ERROR", error_auth_msg, trace_id=self.trace_id)
                raise SolumAPIError(error_auth_msg)

        # 🎯 Tu funcionalidad original intacta a partir de aquí:
        # Pasamos la compañía para resolver u obtener el token correcto
        token = self._ensure_valid_token(company)
        
        # Aseguramos la inyección perfecta de la barra divisoria intermedia
        endpoint_clean = endpoint.lstrip('/')
        url = f"{self.base_url}/{endpoint_clean}"
        
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }

        # 🎯 Registramos el cURL automáticamente en modo DEBUG
        self._log_curl_debug(method, url, headers, json_data, params)

        try:
            response = requests.request(method, url, headers=headers, json=json_data, params=params, timeout=10)
            
            log_loki("INFO", f"Solum API Response Status: {response.status_code}", trace_id=self.trace_id)
            if response.text:
                log_loki("INFO", f"Solum API Response Body:\n{response.text}", trace_id=self.trace_id)

            # SIEMPRE UPDATE tras round-trip Solum para mover ``updated``.
            # Éxito HTTP → id AIMS o fallback correlation cli-/web-api-; timeout → NULL.
            solum_id = resolve_solum_trace_id_for_log(
                response,
                correlation_id=self.trace_id,
                contacted=True,
            )
            self._record_trace_log_solum(
                method=method,
                endpoint=endpoint_clean,
                status_code=response.status_code,
                solum_trace_id=solum_id,
            )
            
            # Si da 404, Solum a veces devuelve un JSON de error o un body vacío. Lo gestionamos explícitamente:
            if response.status_code == 404:
                log_loki("WARNING", f"Solum API returned 404 Not Found for {endpoint}", trace_id=self.trace_id)
                return {"status": "NOT_FOUND", "raw_data": None}
                
            if response.status_code == 401:
                log_loki("WARNING", f"401 Unauthorized for {company}. Invalidating token cache...", trace_id=self.trace_id)
                comp_key = "TWI" if str(company).strip().upper() == "TWI" else "962"
                SolumCoreClient._tokens[comp_key] = None
                return self.request(method, endpoint, json_data, params, company)

            response.raise_for_status()
            return {"status": "SUCCESS", "raw_data": response.json() if response.text else {}}
            
        except requests.exceptions.RequestException as e:
            # Solo forzar NULL si no hubo respuesta HTTP (timeout/conexión).
            # Si raise_for_status falla tras haber registrado ya el correlation, no pisar.
            if getattr(e, "response", None) is None:
                self._record_trace_log_solum(
                    method=method,
                    endpoint=endpoint_clean,
                    status_code=None,
                    solum_trace_id=None,
                    error=str(e),
                )
            error_msg = f"Error during Solum API request [{method} {endpoint}] for {company}: {str(e)}"
            log_loki("ERROR", error_msg, trace_id=self.trace_id)
            raise SolumAPIError(error_msg) from e

    def _record_trace_log_solum(
        self,
        method: str,
        endpoint: str,
        status_code: Optional[int] = None,
        solum_trace_id: Optional[str] = None,
        error: Optional[str] = None,
    ) -> None:
        """
        UPDATE obligatorio tras contactar Solum.

        - Éxito: ``trace_id_solum`` = id AIMS o correlation del flujo Solum.
        - Timeout/error sin respuesta: ``trace_id_solum`` NULL (``updated`` sí avanza).
        """
        try:
            from services.trace_log import TraceLogService

            meta = {"method": method, "endpoint": endpoint}
            if status_code is not None:
                meta["status_code"] = status_code
            if error:
                meta["error"] = error
            if solum_trace_id:
                meta["resolved_from"] = (
                    "response"
                    if solum_trace_id != self.trace_id
                    else "flow_correlation"
                )
            TraceLogService.mark_after_solum_current(
                trace_id_solum=solum_trace_id,
                solum_meta=meta,
                loki_trace_id=self.trace_id,
                correlation_id=self.trace_id,
            )
        except Exception as audit_err:
            log_loki(
                "WARNING",
                f"No se pudo actualizar trace_log tras Solum: {audit_err}",
                trace_id=self.trace_id,
            )
