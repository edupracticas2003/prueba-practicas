# services/solum_antennas.py
import os
from typing import Dict, Any
from services.solum_core import SolumCoreClient, SolumAPIError

class SolumAntennaService:
    """Clase encargada exclusivamente de agrupar las llamadas relativas a Gateways / Antenas."""
    
    def __init__(self, trace_id: str = "internal"):
        self.core = SolumCoreClient(trace_id=trace_id)

    def _clean_mac(self, mac: str) -> str:
        """Asegura que la MAC vaya limpia, plana y en mayúsculas según tu documentación (D02544FFFE1F2EE9)."""
        if not mac:
            return ""
        return mac.replace(":", "").replace("-", "").strip().upper()

    def get_details(self, antenna_mac: str, company: str, store: str) -> Dict[str, Any]:
        """Consume el endpoint v2 detallado controlando de forma segura las respuestas vacías (204)."""
        flat_mac = self._clean_mac(antenna_mac)
        endpoint = f"common/gateway/detail?company={str(company).strip()}&store={str(store).strip()}&gateway={flat_mac}"

        try:
            result = self.core.request("GET", endpoint, company=company)
            
        
            if result["status"] == "SUCCESS":
                raw = result.get("raw_data")
                
                # 🎯 CONTROL DEL 204: Si Solum responde sin contenido, el AP está apagado o desconfigurado
                if not raw:
                    print("⚠️ [SOLUM SERVICE DEBUG] 'raw_data' viene vacío (Posible 204 No Content)")
                    return {
                        "status": "SUCCESS",
                        "status_at": "OFF",  # 🎯 TEXTO PLANO LIMPIO
                        "raw_data": {}
                    }
                
                # Procesamos el 200 OK con datos reales
                solum_status = raw.get("networkStatus")
                
                if str(solum_status).upper() == "CONNECTED":
                    status_visual = "ON"    # 🎯 TEXTO PLANO LIMPIO
                elif str(solum_status).upper() in ("DISCONNECTED", "OFFLINE"):
                    status_visual = "OFF"   # 🎯 TEXTO PLANO LIMPIO
                else:
                    status_visual = str(solum_status).upper() if solum_status else "OFF"

                return {
                    "status": "SUCCESS",
                    "status_at": status_visual,
                    "raw_data": raw
                }
            return result

        except SolumAPIError as e:
            error_str = str(e).upper()
            if "405" in error_str:
                return {"status": "BUSINESS_ERROR", "status_at": "STORE_ERR"}  # 🎯 TEXTO PLANO LIMPIO
            return {"status": "API_ERROR", "status_at": "API_ERR"}             # 🎯 TEXTO PLANO LIMPIO
            
        except Exception as general_err:
            import traceback
            traceback.print_exc() # Imprime el árbol del error para saber exactamente en qué línea falló
            print("🚨"*20 + "\n")
            raise general_err

