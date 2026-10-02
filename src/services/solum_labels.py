# services/solum_labels.py
import traceback
from typing import Dict, Any, Optional
from services.solum_core import SolumCoreClient, SolumAPIError, is_solum_timeout
from tw_logger import log_loki


def combine_status_at(detail_res: Optional[Dict[str, Any]], type_res: Optional[Dict[str, Any]] = None) -> str:
    """STATUS AT = solo labels/detail (currentImage.state). type/info no interviene. HTTP != 200 → ERROR."""
    detail_res = detail_res or {}
    if not detail_res:
        return "UNKNOWN"
    if detail_res.get("status") == "SUCCESS":
        key = str(detail_res.get("status_at") or "UNKNOWN").strip().upper()
        return key or "UNKNOWN"
    return "ERROR"

class SolumLabelService:
    """Clase encargada exclusivamente de agrupar las llamadas relativas a etiquetas / ESL."""

    def __init__(self, trace_id: str = "internal"):
        self.trace_id = trace_id
        self.core = SolumCoreClient(trace_id=trace_id)

    def _clean_mac(self, mac: str) -> str:
        """Asegura que la MAC vaya limpia, plana y en mayúsculas según tu documentación (D02544FFFE1F2EE9)."""
        if not mac:
            return ""
        return mac.replace(":", "").replace("-", "").strip().upper()

    def _label_query(self, company: str, store: str, flat_mac: str) -> str:
        # detail usa label=; type/info documenta labelCode en el body — enviamos ambos.
        return (
            f"company={str(company).strip()}&store={str(store).strip()}"
            f"&label={flat_mac}&labelCode={flat_mac}"
        )

    def _image_state(self, image: Dict[str, Any]) -> str:
        return str(image.get("state") or "").strip().upper()

    def _image_has_status_update(self, image: Dict[str, Any]) -> bool:
        value = image.get("statusUpdateTime")
        if value is None:
            return False
        text = str(value).strip()
        return bool(text) and text.upper() not in ("NULL", "NONE", "N/A")

    def _status_update_time(self, image: Optional[Dict[str, Any]]) -> Optional[str]:
        if not image or not self._image_has_status_update(image):
            return None
        return str(image.get("statusUpdateTime"))

    def _pick_last_connection(self, raw: Dict[str, Any], status_image: Optional[Dict[str, Any]], dicts: list) -> str:
        """LAST CONNECTION = solo statusUpdateTime. Nunca processUpdateTime."""
        timed = self._status_update_time(status_image)
        if timed:
            return timed
        success = self._pick_success_image(raw, dicts)
        timed = self._status_update_time(success)
        if timed:
            return timed
        for img in dicts:
            timed = self._status_update_time(img)
            if timed:
                return timed
        return "N/A"

    def _pick_success_image(self, raw: Dict[str, Any], dicts: list) -> Optional[Dict[str, Any]]:
        """Página SUCCESS con statusUpdateTime (fecha que se mostraba cuando el row era ON)."""
        success = [img for img in dicts if self._image_state(img) in ("SUCCESS", "OK")]
        if not success:
            return None
        active = raw.get("activePage")
        if active is not None:
            for img in success:
                if img.get("index") == active and self._image_has_status_update(img):
                    return img
        with_status = [img for img in success if self._image_has_status_update(img)]
        return (with_status or success)[0]

    def _pick_current_image(self, raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Elige la página de currentImage para STATUS AT. TIMEOUT/ERROR no ceden a un SUCCESS viejo."""
        images = raw.get("currentImage")
        if not isinstance(images, list) or not images:
            return None
        dicts = [img for img in images if isinstance(img, dict)]
        if not dicts:
            return None
        active = raw.get("activePage")
        if active is not None:
            for img in dicts:
                if img.get("index") == active:
                    return img
        conflict = [
            img for img in dicts
            if self._image_state(img) in ("TIMEOUT", "ERROR", "FAIL", "FAILED")
        ]
        if conflict:
            return conflict[0]
        return self._pick_success_image(raw, dicts) or dicts[0]

    def _image_content_url(self, image: Optional[Dict[str, Any]]) -> Optional[str]:
        if not image:
            return None
        content = image.get("content")
        if content is None:
            return None
        text = str(content).strip()
        if not text or text.upper() in ("NULL", "NONE", "N/A"):
            return None
        return text

    def _process_update_time(self, image: Optional[Dict[str, Any]]) -> Optional[str]:
        """Request Date en labels/detail = processUpdateTime del objeto imagen."""
        if not image:
            return None
        value = image.get("processUpdateTime")
        if value is None:
            return None
        text = str(value).strip()
        if not text or text.upper() in ("NULL", "NONE", "N/A"):
            return None
        return text

    def _image_dicts(self, raw: Dict[str, Any], source_key: str) -> list:
        images = (raw or {}).get(source_key)
        if not isinstance(images, list):
            return []
        return [img for img in images if isinstance(img, dict)]

    def _pick_image_from_list(self, raw: Dict[str, Any], dicts: list, prefer_status_logic: bool) -> Optional[Dict[str, Any]]:
        """Elige página: activePage si existe; prioriza content; current usa lógica STATUS AT."""
        if not dicts:
            return None
        preferred = None
        if prefer_status_logic:
            preferred = self._pick_current_image(raw)
        else:
            active = raw.get("activePage")
            if active is not None:
                for img in dicts:
                    if img.get("index") == active:
                        preferred = img
                        break
            if preferred is None:
                preferred = dicts[0]
        if preferred and self._image_content_url(preferred):
            return preferred
        active = raw.get("activePage")
        if active is not None:
            for img in dicts:
                if img.get("index") == active and self._image_content_url(img):
                    return img
        for img in dicts:
            if self._image_content_url(img):
                return img
        return preferred or dicts[0]

    def _pick_display_image(self, raw: Dict[str, Any], source_key: str = "currentImage") -> Optional[Dict[str, Any]]:
        """Página a visualizar en currentImage o previousImage."""
        dicts = self._image_dicts(raw or {}, source_key)
        return self._pick_image_from_list(raw or {}, dicts, prefer_status_logic=(source_key == "currentImage"))

    def resolve_display_image(self, raw: Dict[str, Any], source_key: str = "currentImage") -> Dict[str, Any]:
        """
        Metadatos de visualización desde raw labels/detail.
        Completed Date = statusUpdateTime; Request Date = processUpdateTime
        (campos reales del objeto imagen en detail; la guía AIMS HTML no documenta labels/detail).
        """
        image = self._pick_display_image(raw or {}, source_key=source_key)
        role = "previous" if source_key == "previousImage" else "current"
        if not image:
            return {
                "role": role,
                "source_key": source_key,
                "page": None,
                "state": "UNKNOWN",
                "status_at": "UNKNOWN",
                "completed_date": "N/A",
                "request_date": "N/A",
                "last_connection": "N/A",
                "content_url": None,
                "image": None,
            }
        state = self._image_state(image) or "UNKNOWN"
        if state == "TIMEOUT":
            status_at = "TIMEOUT"
        elif state in ("ERROR", "FAIL", "FAILED"):
            status_at = "ERROR"
        elif state in ("SUCCESS", "OK"):
            status_at = "ON"
        elif state:
            status_at = "OFF"
        else:
            status_at = "UNKNOWN"
        completed = self._status_update_time(image) or "N/A"
        request = self._process_update_time(image) or "N/A"
        return {
            "role": role,
            "source_key": source_key,
            "page": image.get("index"),
            "state": state,
            "status_at": status_at,
            "completed_date": completed,
            "request_date": request,
            "last_connection": completed,
            "content_url": self._image_content_url(image),
            "image": image,
        }

    def resolve_show_images(self, raw: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        """currentImage + previousImage para show-label."""
        return {
            "current": self.resolve_display_image(raw or {}, source_key="currentImage"),
            "previous": self.resolve_display_image(raw or {}, source_key="previousImage"),
        }

    def _map_detail_payload(self, raw: Dict[str, Any]) -> Dict[str, str]:
        """STATUS AT desde currentImage.state; LAST CONNECTION solo statusUpdateTime."""
        images = raw.get("currentImage")
        dicts = [img for img in images if isinstance(img, dict)] if isinstance(images, list) else []
        image = self._pick_current_image(raw)
        if not image:
            return {"status_at": "UNKNOWN", "last_connection": "N/A"}

        state = self._image_state(image)
        if state == "TIMEOUT":
            status_at = "TIMEOUT"
        elif state in ("ERROR", "FAIL", "FAILED"):
            status_at = "ERROR"
        elif state in ("SUCCESS", "OK"):
            status_at = "ON"
        elif state:
            status_at = "OFF"
        else:
            status_at = "UNKNOWN"

        return {
            "status_at": status_at,
            "last_connection": self._pick_last_connection(raw, image, dicts),
        }

    def _unwrap_type_body(self, raw: Any, flat_mac: str) -> Dict[str, Any]:
        """Solum v2 a veces anida propiedades en responseMessage/data o en una lista."""
        if raw is None:
            return {}
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, dict) and self._clean_mac(str(item.get("labelCode") or item.get("label") or "")) == flat_mac:
                    return item
            first = raw[0] if raw else None
            return first if isinstance(first, dict) else {}
        if not isinstance(raw, dict):
            return {}
        if raw.get("totalPage") is not None or "nfc" in raw or raw.get("name"):
            return raw
        for key in ("responseMessage", "data", "result"):
            inner = raw.get(key)
            if isinstance(inner, (dict, list)):
                unwrapped = self._unwrap_type_body(inner, flat_mac)
                if unwrapped:
                    return unwrapped
        return raw

    def _format_nfc(self, nfc_val: Any) -> str:
        if nfc_val is None:
            return "N/A"
        if isinstance(nfc_val, bool):
            return "YES" if nfc_val else "NO"
        text = str(nfc_val).strip().upper()
        if text in ("TRUE", "1", "YES", "Y"):
            return "YES"
        if text in ("FALSE", "0", "NO", "N"):
            return "NO"
        return str(nfc_val)

    def _map_type_info_payload(self, raw: Dict[str, Any]) -> Dict[str, str]:
        """totalPage / nfc / responseMessage salen de labels/type/info, no de detail."""
        total_page = raw.get("totalPage")
        response_message = raw.get("responseMessage")
        if isinstance(response_message, dict):
            response_message = response_message.get("responseMessage") or "SUCCESS"
        return {
            "totalPage": str(total_page) if total_page is not None else "N/A",
            "nfc": self._format_nfc(raw.get("nfc")),
            "responseMessage": str(response_message) if response_message not in (None, "") else "N/A",
        }

    def _empty_type_info(self, status: str) -> Dict[str, Any]:
        return {
            "status": status,
            "totalPage": "N/A",
            "nfc": "N/A",
            "responseMessage": "N/A",
            "raw_data": {},
        }

    def get_details(self, label_mac: str, company: str, store: str) -> Dict[str, Any]:
        """Consume el detalle de etiqueta uniendo el path como gateway/detail sobre SOLUM_API_URL."""
        flat_mac = self._clean_mac(label_mac)
        endpoint = f"common/labels/detail?{self._label_query(company, store, flat_mac)}"

        try:
            result = self.core.request("GET", endpoint, company=company)

            if result["status"] == "SUCCESS":
                raw = result.get("raw_data")

                if not raw:
                    log_loki("WARNING", "Solum labels/detail raw_data vacío (posible 204 No Content)", trace_id=self.trace_id)
                    return {
                        "status": "SUCCESS",
                        "status_at": "OFF",
                        "last_connection": "N/A",
                        "raw_data": {}
                    }

                mapped = self._map_detail_payload(raw)
                return {
                    "status": "SUCCESS",
                    "status_at": mapped["status_at"],
                    "last_connection": mapped["last_connection"],
                    "raw_data": raw
                }
            return {
                "status": result.get("status") or "API_ERROR",
                "status_at": "ERROR",
                "last_connection": "N/A",
                "raw_data": result.get("raw_data"),
            }

        except SolumAPIError as e:
            log_loki("ERROR", f"Solum labels/detail error for {flat_mac}: {e}", trace_id=self.trace_id)
            return {
                "status": "API_ERROR",
                "status_at": "ERROR",
                "last_connection": "N/A",
                "raw_data": {},
            }

        except Exception as general_err:
            log_loki("ERROR", f"Solum labels/detail unexpected error for {flat_mac}: {traceback.format_exc()}", trace_id=self.trace_id)
            raise general_err

    def get_type_info(self, label_mac: str, company: str, store: str) -> Dict[str, Any]:
        """Propiedades de etiqueta (Image Push): common/labels/type/info sobre SOLUM_API_URL."""
        flat_mac = self._clean_mac(label_mac)
        endpoint = f"common/labels/type/info?{self._label_query(company, store, flat_mac)}"
        log_loki("INFO", f"Solum type/info GET {endpoint}", trace_id=self.trace_id)

        try:
            result = self.core.request("GET", endpoint, company=company)
            status = result.get("status")

            if status == "SUCCESS":
                raw = result.get("raw_data")
                body = self._unwrap_type_body(raw, flat_mac)
                if not body:
                    log_loki("WARNING", f"Solum type/info 200 sin propiedades para {flat_mac}", trace_id=self.trace_id)
                    return self._empty_type_info("EMPTY")
                mapped = self._map_type_info_payload(body)
                if mapped["totalPage"] == "N/A" and mapped["nfc"] == "N/A":
                    log_loki("WARNING", f"Solum type/info sin totalPage/nfc para {flat_mac}", trace_id=self.trace_id)
                return {
                    "status": "SUCCESS",
                    "totalPage": mapped["totalPage"],
                    "nfc": mapped["nfc"],
                    "responseMessage": mapped["responseMessage"],
                    "raw_data": raw
                }

            log_loki("WARNING", f"Solum type/info status={status} mac={flat_mac}", trace_id=self.trace_id)
            empty = self._empty_type_info(status or "API_ERROR")
            empty["raw_data"] = result.get("raw_data") or {}
            return empty

        except SolumAPIError as e:
            error_str = str(e).upper()
            log_loki("ERROR", f"Solum type/info error for {flat_mac}: {e}", trace_id=self.trace_id)
            if is_solum_timeout(e):
                return self._empty_type_info("TIMEOUT")
            if "405" in error_str:
                return self._empty_type_info("BUSINESS_ERROR")
            return self._empty_type_info("API_ERROR")

        except Exception as general_err:
            log_loki("ERROR", f"Solum type/info unexpected error for {flat_mac}: {traceback.format_exc()}", trace_id=self.trace_id)
            raise general_err
