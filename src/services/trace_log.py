# services/trace_log.py
"""Persistencia DRY de auditoría en tw_com_labels.trace_log (conexión _COM)."""
from __future__ import annotations

import contextvars
import json
import re
import threading
from typing import Any, Dict, Optional

from database import MySQLConnection
from tw_logger import log_loki

# Fila activa del request/comando en curso (un action lógico → una fila).
_current_row_id: contextvars.ContextVar[Optional[int]] = contextvars.ContextVar(
    "trace_log_row_id", default=None
)
# Fallback para hilos sync de FastAPI (el middleware async no comparte ContextVar).
_tls = threading.local()
# SolumCoreClient.trace_id (cli-/web-api-) → row id, sobrevive al threadpool.
_correlation_rows: Dict[str, int] = {}
_correlation_lock = threading.Lock()

ORIGEN_COMANDO = "comando"
ORIGEN_APLICACION = "aplicacion"
ORIGEN_SISTEMA = "sistema"

_SENSITIVE_KEY_PARTS = (
    "password",
    "passwd",
    "token",
    "authorization",
    "secret",
    "bearer",
    "access_token",
    "api_key",
    "apikey",
    "sas",
)

_BEARER_RE = re.compile(r"(?i)\bBearer\s+\S+")
_SAS_QUERY_RE = re.compile(
    r"(?i)([?&](?:sig|signature|se|sv|sp|spr|srt|ss|st)=)[^&\s]+"
)

# No forman parte de la petición entrante: viven en columnas / meta posterior.
_IDENTITY_PAYLOAD_KEYS = frozenset(
    {
        "trace_id",
        "trace_id_solum",
        "session_trace_id",
        "loki_trace_id",
    }
)


def _is_sensitive_key(key: str) -> bool:
    lower = str(key).lower()
    return any(part in lower for part in _SENSITIVE_KEY_PARTS)


def _redact_string(value: str) -> str:
    text = _BEARER_RE.sub("Bearer ***", value)
    text = _SAS_QUERY_RE.sub(r"\1***", text)
    return text


def sanitize_for_trace(value: Any) -> Any:
    """Elimina secretos/passwords/tokens/Bearer/SAS del payload a persistir."""
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for key, item in value.items():
            if _is_sensitive_key(key):
                out[key] = "***"
            else:
                out[key] = sanitize_for_trace(item)
        return out
    if isinstance(value, list):
        return [sanitize_for_trace(item) for item in value]
    if isinstance(value, str):
        return _redact_string(value)
    return value


def inbound_request_payload(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Payload de INSERT: solo la petición entrante (comando / HTTP).

    No incluye trace_id — el id Solum va a la columna ``trace_id_solum`` en el UPDATE
    tras contactar Solum.
    """
    raw = dict(payload or {})
    for key in _IDENTITY_PAYLOAD_KEYS:
        raw.pop(key, None)
    return sanitize_for_trace(raw)


class TraceLogService:
    """
    INSERT: payload = petición entrante (sin trace_id).

    Tras contactar Solum (éxito o error con round-trip): SIEMPRE UPDATE la fila
    abierta para que ``updated`` avance. ``trace_id_solum`` = id de Solum si
    existe; si no, NULL. Sin contacto Solum: no UPDATE (columna sigue NULL).

    Resolución de fila: ContextVar → thread-local → correlation map (cli-/web-api-).
    """

    def __init__(self, loki_trace_id: Optional[str] = None):
        self.loki_trace_id = loki_trace_id
        self._correlation_id: Optional[str] = None

    @staticmethod
    def current_row_id() -> Optional[int]:
        value = _current_row_id.get()
        if value is not None:
            return value
        return getattr(_tls, "row_id", None)

    @staticmethod
    def bind(row_id: Optional[int]) -> None:
        _current_row_id.set(row_id)
        _tls.row_id = row_id

    @staticmethod
    def clear_bind() -> None:
        _current_row_id.set(None)
        _tls.row_id = None

    @staticmethod
    def register_correlation(correlation_id: Optional[str], row_id: Optional[int]) -> None:
        if not correlation_id or not row_id:
            return
        with _correlation_lock:
            _correlation_rows[str(correlation_id)] = int(row_id)

    @staticmethod
    def unregister_correlation(correlation_id: Optional[str]) -> None:
        if not correlation_id:
            return
        with _correlation_lock:
            _correlation_rows.pop(str(correlation_id), None)

    @staticmethod
    def unregister_row(row_id: Optional[int]) -> None:
        """Limpia todas las correlaciones que apuntan a esta fila."""
        if not row_id:
            return
        with _correlation_lock:
            stale = [key for key, value in _correlation_rows.items() if value == int(row_id)]
            for key in stale:
                _correlation_rows.pop(key, None)

    @classmethod
    def resolve_row_id(
        cls,
        row_id: Optional[int] = None,
        correlation_id: Optional[str] = None,
    ) -> Optional[int]:
        if row_id is not None:
            return row_id
        current = cls.current_row_id()
        if current is not None:
            return current
        if correlation_id:
            with _correlation_lock:
                return _correlation_rows.get(str(correlation_id))
        return None

    def clear(self) -> None:
        if self._correlation_id:
            self.unregister_correlation(self._correlation_id)
            self._correlation_id = None
        self.clear_bind()

    def start(self, origen: str, payload: Optional[Dict[str, Any]] = None) -> Optional[int]:
        """INSERT en trace_log con solo la petición entrante. Devuelve id o None si falla."""
        safe_payload = inbound_request_payload(payload)
        try:
            with MySQLConnection(suffix="_COM", trace_id=self.loki_trace_id) as db:
                row_id = db.ejecutar_consulta(
                    """
                    INSERT INTO tw_com_labels.trace_log (origen, payload)
                    VALUES (%s, CAST(%s AS JSON))
                    """,
                    (origen, json.dumps(safe_payload, ensure_ascii=False, default=str)),
                )
            if isinstance(row_id, int) and row_id > 0:
                log_loki(
                    "INFO",
                    "trace_log INSERT ok",
                    extra={"trace_log_id": row_id, "origen": origen},
                    trace_id=self.loki_trace_id,
                )
                return row_id
            log_loki(
                "WARNING",
                f"trace_log INSERT sin lastrowid usable: {row_id!r}",
                trace_id=self.loki_trace_id,
            )
            return None
        except Exception as err:
            log_loki(
                "ERROR",
                f"trace_log INSERT falló (flujo principal continúa): {err}",
                extra={"origen": origen},
                trace_id=self.loki_trace_id,
            )
            return None

    def mark_after_solum(
        self,
        trace_id_solum: Optional[str] = None,
        row_id: Optional[int] = None,
        correlation_id: Optional[str] = None,
        solum_meta: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        SIEMPRE UPDATE tras contacto Solum: fuerza ``updated`` y escribe
        ``trace_id_solum`` (valor o NULL). Acumula meta en payload.solum_calls.
        """
        target_id = self.resolve_row_id(row_id=row_id, correlation_id=correlation_id)
        if not target_id:
            log_loki(
                "WARNING",
                "trace_log UPDATE omitido: no hay fila bindida/correlacionada",
                extra={"correlation_id": correlation_id},
                trace_id=self.loki_trace_id,
            )
            return False

        solum_value = str(trace_id_solum).strip()[:64] if trace_id_solum else None
        if solum_value == "":
            solum_value = None

        safe_meta = None
        if solum_meta:
            meta = dict(solum_meta)
            for key in _IDENTITY_PAYLOAD_KEYS:
                meta.pop(key, None)
            safe_meta = sanitize_for_trace(meta)

        try:
            with MySQLConnection(suffix="_COM", trace_id=self.loki_trace_id) as db:
                # ``updated`` explícito: si trace_id_solum ya era NULL, ON UPDATE
                # solo no bastaría para mover el timestamp.
                db.ejecutar_consulta(
                    """
                    UPDATE tw_com_labels.trace_log
                    SET trace_id_solum = %s,
                        updated = CURRENT_TIMESTAMP
                    WHERE id = %s
                    """,
                    (solum_value, target_id),
                )
                if safe_meta is not None:
                    try:
                        db.ejecutar_consulta(
                            """
                            UPDATE tw_com_labels.trace_log
                            SET payload = JSON_ARRAY_APPEND(
                                COALESCE(payload, JSON_OBJECT()),
                                '$.solum_calls',
                                CAST(%s AS JSON)
                            ),
                            updated = CURRENT_TIMESTAMP
                            WHERE id = %s
                            """,
                            (
                                json.dumps(safe_meta, ensure_ascii=False, default=str),
                                target_id,
                            ),
                        )
                    except Exception as meta_err:
                        log_loki(
                            "WARNING",
                            f"trace_log solum_calls append falló: {meta_err}",
                            extra={"trace_log_id": target_id},
                            trace_id=self.loki_trace_id,
                        )
            log_loki(
                "INFO",
                "trace_log UPDATE tras Solum ok",
                extra={
                    "trace_log_id": target_id,
                    "trace_id_solum": solum_value,
                },
                trace_id=self.loki_trace_id,
            )
            return True
        except Exception as err:
            log_loki(
                "ERROR",
                f"trace_log UPDATE falló (flujo principal continúa): {err}",
                extra={"trace_log_id": target_id},
                trace_id=self.loki_trace_id,
            )
            return False

    # Alias histórico usado por tests / SolumCoreClient.
    def attach_solum_trace(
        self,
        trace_id_solum: Optional[str],
        row_id: Optional[int] = None,
        solum_meta: Optional[Dict[str, Any]] = None,
        correlation_id: Optional[str] = None,
    ) -> bool:
        return self.mark_after_solum(
            trace_id_solum=trace_id_solum,
            row_id=row_id,
            correlation_id=correlation_id,
            solum_meta=solum_meta,
        )

    @classmethod
    def mark_after_solum_current(
        cls,
        trace_id_solum: Optional[str] = None,
        solum_meta: Optional[Dict[str, Any]] = None,
        loki_trace_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> bool:
        """UPDATE tras Solum usando fila bindida o correlation_id (cli-/web-api-)."""
        return cls(loki_trace_id=loki_trace_id or correlation_id).mark_after_solum(
            trace_id_solum=trace_id_solum,
            correlation_id=correlation_id,
            solum_meta=solum_meta,
        )

    @classmethod
    def attach_solum_trace_current(
        cls,
        trace_id_solum: Optional[str],
        solum_meta: Optional[Dict[str, Any]] = None,
        loki_trace_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> bool:
        return cls.mark_after_solum_current(
            trace_id_solum=trace_id_solum,
            solum_meta=solum_meta,
            loki_trace_id=loki_trace_id,
            correlation_id=correlation_id,
        )

    def begin(
        self,
        origen: str,
        payload: Optional[Dict[str, Any]] = None,
        correlation_id: Optional[str] = None,
    ) -> Optional[int]:
        """INSERT + bind (+ correlation para que SolumCoreClient encuentre la fila)."""
        row_id = self.start(origen, payload)
        self.bind(row_id)
        if correlation_id and row_id:
            self.register_correlation(correlation_id, row_id)
            self._correlation_id = str(correlation_id)
        return row_id


def adopt_request_trace_log(request: Any, correlation_id: str) -> Optional[int]:
    """
    Re-bind en el hilo del handler sync de FastAPI (el middleware async no
    comparte ContextVar) y registra correlation_id → row para SolumCoreClient.
    """
    state = getattr(request, "state", None)
    row_id = getattr(state, "trace_log_row_id", None) if state is not None else None
    if not row_id:
        return None
    TraceLogService.bind(row_id)
    TraceLogService.register_correlation(correlation_id, row_id)
    return row_id
