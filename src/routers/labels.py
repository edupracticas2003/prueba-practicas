# routers/labels.py
import os
import uuid
from fastapi import APIRouter, HTTPException, Header, Depends, Request
from pydantic import BaseModel, Field
from database import MySQLConnection
from tw_logger import log_loki
from services.solum_core import is_solum_timeout
from services.solum_labels import SolumLabelService, combine_status_at
from services.auth_service import AuthService
from services.trace_log import adopt_request_trace_log


router = APIRouter(prefix="/api/v1/labels", tags=["Labels"])

class LabelQueryRequest(BaseModel):
    db_name: str = Field(..., description="Database name (e.g., 'madrid'). Use **'*'** to scan all available system databases.", examples=["madrid", "*"])
    label_status: bool = Field(..., description="Filter by local database status: `True` (Active) / `False` (Inactive).")
    solum_status: bool = Field(default=False, description="Query the external Solum API for real-time status? True/False.")

def verificar_token_api(token_header: str = Header(None, alias="Token-Header", description="Mandatory Bearer authentication token.")):
    """Extrae el token directamente desde la cabecera sin bloqueos de Swagger."""
    if not token_header:
        raise HTTPException(status_code=401, detail="Falta el token en la cabecera 'Token-Header'")

    token = token_header.replace("Bearer ", "").replace("bearer ", "").strip()

    auth = AuthService(trace_id="api-token-validation")
    if not auth.validar_token(token):
        raise HTTPException(status_code=401, detail="Token inválido, ausente o expirado (Validez: 24h)")

    os.environ["INCOMING_API_TOKEN"] = token
    return token

@router.post(
    "/query-status",
    summary="Query label and infrastructure status",
    responses={
        200: {
            "description": "Successful operation. Returns execution summary and the dynamic dataset array.",
            "content": {
                "application/json": {
                    "example": {
                        "success": True,
                        "trace_id": "web-api-abc123xyz789",
                        "count": 1,
                        "labels": [
                            {
                                "id": 42,
                                "etiqueta": "Room-101",
                                "mac": "00:11:22:33:44:55",
                                "activo": 1,
                                "db_name": "madrid",
                                "status_tw": "ON",
                                "status_at": "ON",
                                "totalPage": "7",
                                "nfc": "YES",
                                "responseMessage": "SUCCESS",
                                "last_connection": "2026-09-09 14:15:11.466",
                                "raw_solum_data": {}
                            }
                        ]
                    }
                }
            }
        },
        401: {"description": "Unauthorized. Token-Header is missing, poorly formatted, or expired."},
        404: {"description": "No labels or valid databases found matching the provided criteria."},
        500: {"description": "Internal Server Error while querying the database schemas."}
    }
)
def api_query_labels_status(
    payload: LabelQueryRequest,
    request: Request,
    token: str = Depends(verificar_token_api),
):
    """
    Performs a bulk or specific query over the registered labels across multi-tenant database schemas (`bd_feeltourist_*`).

    ### Endpoint Flow:
    1. **Database Resolution:** If `db_name` is `*`, the system dynamically fetches all matching schemas from `information_schema`. If a specific name is provided, it targets only that single instance.
    2. **Local Extraction:** Retrieves labels filtered by their active status from the `dispositivos_etiquetas` table.
    3. **Optional Enrichment (Solum API):** If `solum_status` is `True`, credentials are fetched from `bd_twistic_sat.clients_centers`. `labels/detail` supplies STATUS AT / last connection; `labels/type/info` supplies `totalPage`, `nfc`, and `responseMessage`.
    4. **Sorting:** The final results are returned sorted alphabetically by the label name.
    """
    trace_id = f"web-api-{uuid.uuid4().hex[:12]}"
    adopt_request_trace_log(request, trace_id)
    pref_db = 'bd_feeltourist_'
    status_filter = 1 if payload.label_status else 0


    try:
        with MySQLConnection(suffix="_DATA", trace_id=trace_id) as db:
            if payload.db_name == "*":
                query_dbs = "SELECT schema_name FROM information_schema.schemata WHERE schema_name LIKE %s"
                res_dbs = db.ejecutar_consulta(query_dbs, (f"{pref_db}%",))

                if not res_dbs:
                    raise HTTPException(status_code=404, detail=f"No databases found with prefix '{pref_db}'")

                databases_to_scan = [
                    row.get('schema_name') if isinstance(row, dict) else row
                    for row in res_dbs
                ]
            else:
                databases_to_scan = [f"{pref_db}{payload.db_name}"]

            resultados_totales = []
            label_service = SolumLabelService(trace_id=trace_id) if payload.solum_status else None

            for target_db in databases_to_scan:
                company_db = ""
                store_db = ""

                if payload.solum_status:
                    try:
                        query_cfg = "SELECT labels_user, labels_password FROM bd_twistic_sat.clients_centers WHERE db_name = %s LIMIT 1"
                        res_cfg = db.ejecutar_consulta(query_cfg, (target_db,))
                        if res_cfg:
                            row_cfg = res_cfg[0] if isinstance(res_cfg, list) and len(res_cfg) > 0 else res_cfg

                            company_db = str(row_cfg.get('labels_user', '')).strip()
                            store_db = str(row_cfg.get('labels_password', '')).strip()
                    except Exception as cfg_err:
                        print(f"⚠️ [API ERROR] Fallo al extraer credenciales para {target_db}: {cfg_err}")
                        continue

                query = f"SELECT id, etiqueta, mac, activo FROM `{target_db}`.dispositivos_etiquetas WHERE activo = %s"
                params = (status_filter,)

                try:
                    resultados = db.ejecutar_consulta(query, params)
                    if resultados:
                        db_limpia = target_db.replace(pref_db, "")
                        for row in resultados:
                            row_dict = {
                                "id": row.get("id"),
                                "etiqueta": row.get("etiqueta"),
                                "mac": row.get("mac"),
                                "activo": row.get("activo"),
                                "db_name": db_limpia,
                                "status_tw": "ON" if row.get("activo") == 1 else "OFF",
                                "status_at": "UNKNOWN",
                                "totalPage": "N/A",
                                "nfc": "N/A",
                                "responseMessage": "N/A",
                                "last_connection": "N/A",
                                "raw_solum_data": {}
                            }

                            if payload.solum_status and row_dict["mac"] and company_db and store_db:
                                try:
                                    api_res = label_service.get_details(
                                        label_mac=row_dict["mac"],
                                        company=company_db,
                                        store=store_db
                                    )
                                    type_res = {}
                                    try:
                                        type_res = label_service.get_type_info(
                                            label_mac=row_dict["mac"],
                                            company=company_db,
                                            store=store_db
                                        ) or {}
                                    except Exception as type_err:
                                        log_loki("ERROR", f"type/info failed for {row_dict['mac']}: {type_err}")
                                        type_res = {"status": "TIMEOUT" if is_solum_timeout(type_err) else "API_ERROR"}
                                    row_dict["status_at"] = combine_status_at(api_res)
                                    if api_res.get("status") == "SUCCESS":
                                        raw_info = api_res.get("raw_data", {}) or {}
                                        row_dict["last_connection"] = api_res.get("last_connection") or "N/A"
                                        row_dict["raw_solum_data"] = raw_info
                                    if type_res.get("status") == "SUCCESS":
                                        row_dict["totalPage"] = type_res.get("totalPage") if type_res.get("totalPage") not in (None, "") else "N/A"
                                        row_dict["nfc"] = type_res.get("nfc") if type_res.get("nfc") not in (None, "") else "N/A"
                                        row_dict["responseMessage"] = type_res.get("responseMessage") or "N/A"
                                    elif type_res.get("status") == "NOT_FOUND":
                                        row_dict["totalPage"] = row_dict["nfc"] = row_dict["responseMessage"] = "NOT REG"
                                    elif type_res.get("status"):
                                        fail_txt = "ERROR" if type_res.get("status") != "EMPTY" else "EMPTY"
                                        row_dict["totalPage"] = row_dict["nfc"] = row_dict["responseMessage"] = fail_txt
                                except Exception:
                                    row_dict["status_at"] = "ERROR"

                            resultados_totales.append(row_dict)
                except Exception:
                    continue

            if not resultados_totales:
                raise HTTPException(status_code=404, detail="No labels found matching the criteria.")

            resultados_totales.sort(key=lambda x: str(x['etiqueta'] or '').lower())

            return {
                "success": True,
                "trace_id": trace_id,
                "count": len(resultados_totales),
                "labels": resultados_totales
            }

    except HTTPException as http_ex:
        raise http_ex
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal Server Error: {str(e)}")
