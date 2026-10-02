# routers/antennas.py
import os
import uuid
from fastapi import APIRouter, HTTPException, Header, Depends, Request
from pydantic import BaseModel, Field
from typing import Optional
from database import MySQLConnection
from services.solum_antennas import SolumAntennaService
from services.auth_service import AuthService
from services.trace_log import adopt_request_trace_log


router = APIRouter(prefix="/api/v1/antennas", tags=["Antennas"])

class AntennaQueryRequest(BaseModel):
    db_name: str = Field(..., description="Database name (e.g., 'madrid'). Use **'*'** to scan all available system databases.", examples=["madrid", "*"])
    antenna_status: bool = Field(..., description="Filter by local database status: `True` (Active) / `False` (Inactive).")
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
    summary="Query antenna and infrastructure status",
    responses={
        200: {
            "description": "Successful operation. Returns execution summary and the dynamic dataset array.",
            "content": {
                "application/json": {
                    "example": {
                        "success": True,
                        "trace_id": "web-api-abc123xyz789",
                        "count": 1,
                        "antennas": [
                            {
                                "id": 42,
                                "nombre": "AP-Main-Entrance",
                                "mac": "00:11:22:33:44:55",
                                "activo": 1,
                                "db_name": "madrid",
                                "status_tw": "ON",
                                "status_at": "ON",
                                "ch": "11",
                                "firmware": "v2.4.1",
                                "last_connection": "2026-09-15 08:00:00",
                                "raw_solum_data": {}
                            }
                        ]
                    }
                }
            }
        },
        401: {"description": "Unauthorized. Token-Header is missing, poorly formatted, or expired."},
        404: {"description": "No antennas or valid databases found matching the provided criteria."},
        500: {"description": "Internal Server Error while querying the database schemas."}
    }
)
def api_query_antennas_status(
    payload: AntennaQueryRequest,
    request: Request,
    token: str = Depends(verificar_token_api),
):
    """
    Performs a bulk or specific query over the registered antennas across multi-tenant database schemas (`bd_feeltourist_*`).

    ### Endpoint Flow:
    1. **Database Resolution:** If `db_name` is `*`, the system dynamically fetches all matching schemas from `information_schema`. If a specific name is provided, it targets only that single instance.
    2. **Local Extraction:** Retrieves antennas filtered by their active status from the `etiquetas_aps` table.
    3. **Optional Enrichment (Solum API):** If `solum_status` is `True`, secure credentials are fetched from `bd_twistic_sat.clients_centers` and an external HTTP request is triggered per MAC address to retrieve real-time metrics (Channel, Firmware, Last Connection).
    4. **Sorting:** The final results are returned sorted alphabetically by the antenna name.
    """
    trace_id = f"web-api-{uuid.uuid4().hex[:12]}"
    adopt_request_trace_log(request, trace_id)
    pref_db = 'bd_feeltourist_'
    status_filter = 1 if payload.antenna_status else 0

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
            antenna_service = SolumAntennaService(trace_id=trace_id) if payload.solum_status else None

            for target_db in databases_to_scan:
                company_db = ""
                store_db = ""
                
                if payload.solum_status:
                    try:
                        query_cfg = "SELECT labels_user, labels_password FROM bd_twistic_sat.clients_centers WHERE db_name = %s LIMIT 1"
                        res_cfg = db.ejecutar_consulta(query_cfg, (target_db,))
                        if res_cfg:
                            # 🎯 CORRECCIÓN: Extraemos el primer diccionario de la lista devuelta por fetchall()
                            row_cfg = res_cfg[0] if isinstance(res_cfg, list) and len(res_cfg) > 0 else res_cfg
                            
                            company_db = str(row_cfg.get('labels_user', '')).strip()
                            store_db = str(row_cfg.get('labels_password', '')).strip()
                    except Exception as cfg_err:
                        print(f"⚠️ [API ERROR] Fallo al extraer credenciales para {target_db}: {cfg_err}")
                        continue

                query = f"SELECT id, nombre, mac, activo FROM `{target_db}`.etiquetas_aps WHERE activo = %s"
                params = (status_filter,)
                
                try:
                    resultados = db.ejecutar_consulta(query, params)
                    if resultados:
                        db_limpia = target_db.replace(pref_db, "")
                        for row in resultados:
                            row_dict = {
                                "id": row.get("id"),
                                "nombre": row.get("nombre"),
                                "mac": row.get("mac"),
                                "activo": row.get("activo"),
                                "db_name": db_limpia,
                                "status_tw": "ON" if row.get("activo") == 1 else "OFF",
                                "status_at": "UNKNOWN",
                                "ch": "N/A",
                                "firmware": "N/A",
                                "last_connection": "N/A",
                                "raw_solum_data": {}
                            }

                            if payload.solum_status and row_dict["mac"] and company_db and store_db:
                                try:
                                    api_res = antenna_service.get_details(
                                        antenna_mac=row_dict["mac"],
                                        company=company_db,
                                        store=store_db
                                    )
                                    if api_res.get("status") == "NOT_FOUND":
                                        row_dict["status_at"] = "NOT REG"
                                    elif api_res.get("status") == "SUCCESS":
                                        row_dict["status_at"] = api_res.get("status_at", "OFF")
                                        raw_info = api_res.get("raw_data", {})
                                        row_dict["ch"] = raw_info.get("channel", "N/A")
                                        row_dict["firmware"] = raw_info.get("antennaVersion", "N/A")
                                        row_dict["last_connection"] = raw_info.get("lastConnectedTime", "N/A")
                                        row_dict["raw_solum_data"] = raw_info
                                except Exception:
                                    row_dict["status_at"] = "API_ERR"
                            
                            resultados_totales.append(row_dict)
                except Exception:
                    continue

            if not resultados_totales:
                raise HTTPException(status_code=404, detail="No antennas found matching the criteria.")

            resultados_totales.sort(key=lambda x: str(x['nombre']).lower())

            return {
                "success": True,
                "trace_id": trace_id,
                "count": len(resultados_totales),
                "antennas": resultados_totales
            }

    except HTTPException as http_ex:
        raise http_ex
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal Server Error: {str(e)}")
