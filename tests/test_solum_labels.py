import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from unittest.mock import MagicMock

SRC_DIR = os.path.join(os.path.dirname(__file__), "..", "src")
sys.path.insert(0, os.path.abspath(SRC_DIR))

from services.solum_core import SolumAPIError, is_solum_timeout
from services.solum_labels import SolumLabelService, combine_status_at

DETAIL_FIXTURE = {
    "labelCode": "0AF4D1C0B291",
    "isDualSidedLabel": False,
    "width": 384,
    "height": 168,
    "activePage": 1,
    "previousImage": [
        {"index": 1, "state": "SUCCESS", "processUpdateTime": "2026-09-09 14:14:40.000", "statusUpdateTime": "2026-09-09 14:15:11.466"}
    ],
    "currentImage": [
        {"index": 1, "state": "SUCCESS", "processUpdateTime": "2026-09-09 14:14:40.000", "statusUpdateTime": "2026-09-09 14:15:11.466"}
    ],
    "responseCode": "200",
    "responseMessage": "OK",
    "latestBatchInfo": {"txSequence": "", "type": "", "batchEventTime": ""},
}

TYPE_INFO_FIXTURE = {
    "labelCode": "03704160B297",
    "name": "NEWTON_GRAPHIC_2_9_RED_NFC",
    "displayWidth": 384,
    "displayHeight": 168,
    "totalPage": 7,
    "colorType": "TERNARY_RED",
    "resolution": 145,
    "nfc": True,
    "responseCode": "200",
    "responseMessage": "SUCCESS",
}


class SolumLabelServiceTests(unittest.TestCase):
    def _service_with_payload(self, raw):
        service = SolumLabelService(trace_id="cli-test")
        service.core = MagicMock()
        service.core.request.return_value = {"status": "SUCCESS", "raw_data": raw}
        return service

    def test_clean_mac_and_relative_detail_endpoint(self):
        service = self._service_with_payload(DETAIL_FIXTURE)
        result = service.get_details("aa:bb:cc:dd:ee:ff", "TWI", "1024")

        self.assertEqual(result["status_at"], "ON")
        method = service.core.request.call_args[0][0]
        endpoint = service.core.request.call_args[0][1]
        self.assertEqual(method, "GET")
        self.assertTrue(endpoint.startswith("common/labels/detail?"))
        self.assertNotIn("api/v2", endpoint)
        self.assertNotIn("common/api/v2", endpoint)
        self.assertIn("label=AABBCCDDEEFF", endpoint)

        base_url = "https://eu.common.solumesl.com/common/api/v2"
        full_url = f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}"
        self.assertEqual(full_url.count("api/v2"), 1)
        self.assertNotIn("/common/api/v2/common/api/v2/", full_url)

    def test_type_info_relative_path_and_mapping(self):
        service = self._service_with_payload(TYPE_INFO_FIXTURE)
        result = service.get_type_info("aa:bb:cc:dd:ee:ff", "TWI", "LAB01")
        endpoint = service.core.request.call_args[0][1]
        self.assertTrue(endpoint.startswith("common/labels/type/info?"))
        self.assertNotIn("api/v2", endpoint)
        self.assertIn("company=TWI", endpoint)
        self.assertIn("store=LAB01", endpoint)
        self.assertIn("label=AABBCCDDEEFF", endpoint)
        self.assertIn("labelCode=AABBCCDDEEFF", endpoint)
        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(result["totalPage"], "7")
        self.assertEqual(result["nfc"], "YES")
        self.assertEqual(result["responseMessage"], "SUCCESS")
        self.assertNotEqual(result.get("status_at"), "ON")

    def test_type_info_nfc_false(self):
        raw = dict(TYPE_INFO_FIXTURE)
        raw["nfc"] = False
        service = self._service_with_payload(raw)
        result = service.get_type_info("03704160B297", "TWI", "LAB01")
        self.assertEqual(result["nfc"], "NO")

    def test_type_info_nested_response_message(self):
        wrapped = {"responseCode": "200", "responseMessage": dict(TYPE_INFO_FIXTURE)}
        service = self._service_with_payload(wrapped)
        result = service.get_type_info("03704160B297", "TWI", "LAB01")
        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(result["totalPage"], "7")
        self.assertEqual(result["nfc"], "YES")
        self.assertEqual(result["responseMessage"], "SUCCESS")
        service = self._service_with_payload(DETAIL_FIXTURE)
        result = service.get_details("0AF4D1C0B291", "TWI", "LAB01")
        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(result["status_at"], "ON")
        self.assertEqual(result["last_connection"], "2026-09-09 14:15:11.466")
        self.assertNotIn("battery", result)
        self.assertNotIn("firmware", result)

    def test_detail_other_image_state_is_off(self):
        raw = dict(DETAIL_FIXTURE)
        raw["currentImage"] = [{"index": 1, "state": "PROCESSING", "statusUpdateTime": "2026-09-09 14:15:11.466"}]
        service = self._service_with_payload(raw)
        result = service.get_details("0AF4D1C0B291", "TWI", "LAB01")
        self.assertEqual(result["status_at"], "OFF")

    def test_detail_mixed_timeout_pages_not_stale_success(self):
        raw = {
            "labelCode": "0758892BB696",
            "activePage": None,
            "currentImage": [
                {"index": 1, "state": "TIMEOUT", "statusUpdateTime": None, "processUpdateTime": "2026-09-18 02:38:14.000"},
                {"index": 2, "state": "TIMEOUT", "statusUpdateTime": None},
                {"index": 3, "state": "TIMEOUT", "statusUpdateTime": None},
                {"index": 4, "state": "SUCCESS", "statusUpdateTime": "2025-10-09 17:37:50.815"},
                {"index": 5, "state": "SUCCESS", "statusUpdateTime": "2025-10-09 17:37:50.815"},
                {"index": 6, "state": "SUCCESS"},
                {"index": 7, "state": "SUCCESS"},
            ],
            "responseCode": "200",
            "responseMessage": "OK",
        }
        service = self._service_with_payload(raw)
        result = service.get_details("0758892BB696", "TWI", "LAB01")
        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(result["status_at"], "TIMEOUT")
        self.assertEqual(result["last_connection"], "2025-10-09 17:37:50.815")
        self.assertNotIn("2026-09-18", result["last_connection"])
        self.assertNotEqual(result["status_at"], "ON")
        self.assertEqual(
            combine_status_at(result, {"status": "SUCCESS", "responseMessage": "SUCCESS"}),
            "TIMEOUT",
        )

    def test_timeout_without_any_status_update_time_is_na(self):
        raw = {
            "labelCode": "0758892BB696",
            "activePage": None,
            "currentImage": [
                {"index": 1, "state": "TIMEOUT", "statusUpdateTime": None, "processUpdateTime": "2026-09-18 02:38:14.000"},
            ],
            "responseCode": "200",
            "responseMessage": "OK",
        }
        service = self._service_with_payload(raw)
        result = service.get_details("0758892BB696", "TWI", "LAB01")
        self.assertEqual(result["status_at"], "TIMEOUT")
        self.assertEqual(result["last_connection"], "N/A")
        self.assertNotIn("2026-09-18", result["last_connection"])

    def test_success_last_connection_uses_status_update_time(self):
        raw = dict(DETAIL_FIXTURE)
        raw["activePage"] = None
        raw["currentImage"] = [
            {
                "index": 1,
                "state": "SUCCESS",
                "processUpdateTime": "2026-09-18 02:38:14.000",
                "statusUpdateTime": "2025-10-09 17:37:50.815",
            },
            {
                "index": 2,
                "state": "SUCCESS",
                "processUpdateTime": "2026-09-18 03:00:00.000",
                "statusUpdateTime": "2025-10-09 17:37:50.815",
            },
        ]
        service = self._service_with_payload(raw)
        result = service.get_details("0758892BB696", "TWI", "LAB01")
        self.assertEqual(result["status_at"], "ON")
        self.assertEqual(result["last_connection"], "2025-10-09 17:37:50.815")
        self.assertNotIn("2026-09-18", result["last_connection"])

    def test_success_last_connection_prefers_page_with_status_update_time(self):
        raw = dict(DETAIL_FIXTURE)
        raw["activePage"] = None
        raw["currentImage"] = [
            {"index": 1, "state": "SUCCESS", "processUpdateTime": "2026-09-18 02:38:14.000", "statusUpdateTime": None},
            {"index": 2, "state": "SUCCESS", "statusUpdateTime": "2025-10-09 17:37:50.815"},
        ]
        service = self._service_with_payload(raw)
        result = service.get_details("0758892BB696", "TWI", "LAB01")
        self.assertEqual(result["status_at"], "ON")
        self.assertEqual(result["last_connection"], "2025-10-09 17:37:50.815")

    def test_combine_status_at_type_success_does_not_override_detail_timeout(self):
        self.assertEqual(
            combine_status_at(
                {
                    "status": "SUCCESS",
                    "status_at": "TIMEOUT",
                    "last_connection": "2025-10-09 17:37:50.815",
                },
                {"status": "SUCCESS", "responseMessage": "SUCCESS"},
            ),
            "TIMEOUT",
        )
        self.assertNotEqual(
            combine_status_at(
                {"status": "SUCCESS", "status_at": "ERROR"},
                {"status": "SUCCESS", "responseMessage": "SUCCESS"},
            ),
            "ON",
        )

    def test_http_200_without_current_image_is_unknown_not_off(self):
        service = self._service_with_payload({"responseCode": "200", "responseMessage": "OK", "labelCode": "0AF4D1C0B291"})
        result = service.get_details("0AF4D1C0B291", "TWI", "LAB01")
        self.assertEqual(result["status_at"], "UNKNOWN")
        self.assertEqual(result["last_connection"], "N/A")

    def test_type_info_payload_is_not_used_as_status(self):
        service = self._service_with_payload(TYPE_INFO_FIXTURE)
        result = service.get_details("03704160B297", "TWI", "LAB01")
        self.assertEqual(result["status_at"], "UNKNOWN")
        self.assertNotEqual(result["status_at"], "ON")

    def test_combine_status_at_ignores_type_info(self):
        self.assertEqual(
            combine_status_at(
                {"status": "SUCCESS", "status_at": "OFF", "last_connection": "2026-09-17 06:07:34"},
                {"status": "SUCCESS", "responseMessage": "SUCCESS"},
            ),
            "OFF",
        )
        self.assertEqual(
            combine_status_at(
                {"status": "SUCCESS", "status_at": "ON"},
                {"status": "API_ERROR"},
            ),
            "ON",
        )
        self.assertEqual(
            combine_status_at(
                {"status": "SUCCESS", "status_at": "OFF"},
                {"status": "TIMEOUT"},
            ),
            "OFF",
        )

    def test_combine_status_at_unknown_without_solum(self):
        self.assertEqual(combine_status_at({}, {}), "UNKNOWN")

    def test_combine_status_at_http_failure_is_error(self):
        self.assertEqual(
            combine_status_at({"status": "API_ERROR", "status_at": "API_ERR"}, {"status": "SUCCESS"}),
            "ERROR",
        )
        self.assertEqual(
            combine_status_at({"status": "NOT_FOUND"}, {}),
            "ERROR",
        )
        self.assertEqual(
            combine_status_at({"status": "TIMEOUT", "status_at": "TIMEOUT"}, {}),
            "ERROR",
        )

    def test_is_solum_timeout_detects_connect_and_read(self):
        wrapped = SolumAPIError(
            "Error during Solum API request [GET common/labels/type/info] for TWI: "
            "HTTPSConnectionPool(host='eu.common.solumesl.com', port=443): "
            "Max retries exceeded with url: /common/api/v2/common/labels/type/info "
            "(Caused by ConnectTimeoutError(..., connect timeout=10))"
        )
        self.assertTrue(is_solum_timeout(wrapped))
        self.assertTrue(is_solum_timeout(Exception("ConnectTimeout")))
        self.assertTrue(is_solum_timeout(Exception("ReadTimeout")))
        self.assertFalse(is_solum_timeout(SolumAPIError("HTTP 500 Internal Server Error")))
        self.assertFalse(is_solum_timeout(Exception("connection refused")))

    def test_type_info_solum_timeout_returns_timeout_status(self):
        service = SolumLabelService(trace_id="cli-test")
        service.core = MagicMock()
        service.core.request.side_effect = SolumAPIError(
            "Error during Solum API request: ConnectTimeoutError to eu.common.solumesl.com (connect timeout=10)"
        )
        result = service.get_type_info("AABBCCDDEEFF", "TWI", "1024")
        self.assertEqual(result["status"], "TIMEOUT")
        self.assertNotEqual(result["status"], "SUCCESS")

    def test_detail_solum_timeout_returns_timeout_status_at(self):
        service = SolumLabelService(trace_id="cli-test")
        service.core = MagicMock()
        service.core.request.side_effect = SolumAPIError("Read timed out")
        result = service.get_details("AABBCCDDEEFF", "TWI", "1024")
        self.assertEqual(result["status"], "API_ERROR")
        self.assertEqual(result["status_at"], "ERROR")

    def test_combine_status_at_http_failure_not_on(self):
        self.assertEqual(
            combine_status_at(
                {"status": "API_ERROR", "status_at": "ERROR"},
                {"status": "API_ERROR"},
            ),
            "ERROR",
        )

    def test_empty_payload_is_off(self):
        service = self._service_with_payload({})
        result = service.get_details("AABBCCDDEEFF", "962", "1")
        self.assertEqual(result["status_at"], "OFF")

    def test_not_found_passthrough(self):
        service = SolumLabelService(trace_id="cli-test")
        service.core = MagicMock()
        service.core.request.return_value = {"status": "NOT_FOUND", "raw_data": None}
        result = service.get_details("AABBCCDDEEFF", "TWI", "1024")
        self.assertEqual(result["status"], "NOT_FOUND")
        self.assertEqual(result["status_at"], "ERROR")

    def test_http_error_on_405(self):
        service = SolumLabelService(trace_id="cli-test")
        service.core = MagicMock()
        service.core.request.side_effect = SolumAPIError("HTTP 405 Method Not Allowed")
        result = service.get_details("AABBCCDDEEFF", "TWI", "1024")
        self.assertEqual(result["status"], "API_ERROR")
        self.assertEqual(result["status_at"], "ERROR")

    def test_type_info_405_logs_not_stdout(self):
        service = SolumLabelService(trace_id="cli-test")
        service.core = MagicMock()
        service.core.request.side_effect = SolumAPIError(
            "Error during Solum API request [GET common/labels/type/info?company=TWI] "
            "for TWI: 405 Client Error: Method Not Allowed for url: "
            "https://eu.common.solumesl.com/common/api/v2/common/labels/type/info"
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            result = service.get_type_info("09D965B69E", "TWI", "1024")
        out = buf.getvalue()
        self.assertEqual(result["status"], "BUSINESS_ERROR")
        self.assertNotIn("[SOLUM TYPE INFO]", out)
        self.assertNotIn("Method Not Allowed", out)
        self.assertNotIn("type/info", out)

    def test_empty_detail_does_not_print_debug(self):
        service = self._service_with_payload({})
        buf = io.StringIO()
        with redirect_stdout(buf):
            service.get_details("AABBCCDDEEFF", "962", "1")
        self.assertNotIn("[SOLUM SERVICE DEBUG]", buf.getvalue())
        self.assertNotIn("raw_data", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
