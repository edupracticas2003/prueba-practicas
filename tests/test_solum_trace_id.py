import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

SRC_DIR = os.path.join(os.path.dirname(__file__), "..", "src")
sys.path.insert(0, os.path.abspath(SRC_DIR))

from services.solum_core import (
    SolumAPIError,
    SolumCoreClient,
    extract_solum_trace_id,
    resolve_solum_trace_id_for_log,
)
from services.trace_log import TraceLogService

# Forma real AIMS de gateway/detail y labels/detail (guía + fixtures del repo).
AIMS_DETAIL_OK = {
    "labelCode": "0AF4D1C0B291",
    "networkStatus": "CONNECTED",
    "responseCode": "200",
    "responseMessage": "OK",
}


class ExtractSolumTraceIdTests(unittest.TestCase):
    def test_from_header(self):
        response = MagicMock()
        response.headers = {"X-Trace-Id": "solum-hdr-1"}
        response.text = ""
        self.assertEqual(extract_solum_trace_id(response), "solum-hdr-1")

    def test_from_custom_batch_id(self):
        response = MagicMock()
        response.headers = {}
        response.text = json.dumps(
            {"responseCode": "200", "responseMessage": "SUCCESS", "customBatchId": "c135e166-933a-4f1f-950c-64de0924c86c"}
        )
        response.json.return_value = json.loads(response.text)
        self.assertEqual(
            extract_solum_trace_id(response),
            "c135e166-933a-4f1f-950c-64de0924c86c",
        )

    def test_from_latest_batch_tx_sequence(self):
        response = MagicMock()
        response.headers = {}
        body = {
            "responseCode": "200",
            "responseMessage": "OK",
            "latestBatchInfo": {"txSequence": "1884BDC768B91FE00", "type": "ARTICLE"},
        }
        response.text = json.dumps(body)
        response.json.return_value = body
        self.assertEqual(extract_solum_trace_id(response), "1884BDC768B91FE00")

    def test_aims_detail_ok_has_no_trace_field(self):
        response = MagicMock()
        response.headers = {"Content-Type": "application/json"}
        response.text = json.dumps(AIMS_DETAIL_OK)
        response.json.return_value = dict(AIMS_DETAIL_OK)
        # responseMessage "OK" no es un id.
        self.assertIsNone(extract_solum_trace_id(response))

    def test_resolve_success_falls_back_to_flow_correlation(self):
        response = MagicMock()
        response.headers = {}
        response.text = json.dumps(AIMS_DETAIL_OK)
        response.json.return_value = dict(AIMS_DETAIL_OK)
        resolved = resolve_solum_trace_id_for_log(
            response,
            correlation_id="cli-deadbeef12",
            contacted=True,
        )
        self.assertEqual(resolved, "cli-deadbeef12")

    def test_resolve_timeout_is_null(self):
        self.assertIsNone(
            resolve_solum_trace_id_for_log(
                None,
                correlation_id="cli-deadbeef12",
                contacted=False,
            )
        )

    def test_resolve_prefers_batch_id_over_correlation(self):
        response = MagicMock()
        response.headers = {}
        body = {"customBatchId": "batch-abc", "responseMessage": "SUCCESS"}
        response.text = json.dumps(body)
        response.json.return_value = body
        self.assertEqual(
            resolve_solum_trace_id_for_log(
                response, correlation_id="cli-deadbeef12", contacted=True
            ),
            "batch-abc",
        )


class SolumCoreTraceLogTests(unittest.TestCase):
    def tearDown(self):
        TraceLogService.clear_bind()
        TraceLogService.unregister_correlation("cli-aabbccddee11")
        SolumCoreClient._tokens = {"TWI": None, "962": None}
        SolumCoreClient._tokens_expires_at = {"TWI": 0.0, "962": 0.0}

    def test_successful_aims_detail_writes_flow_correlation(self):
        client = SolumCoreClient(trace_id="cli-aabbccddee11")
        response = MagicMock()
        response.status_code = 200
        response.headers = {}
        response.text = json.dumps(AIMS_DETAIL_OK)
        response.json.return_value = dict(AIMS_DETAIL_OK)

        with patch.object(client, "_ensure_valid_token", return_value="tok"), \
             patch.object(client, "_log_curl_debug"), \
             patch("services.solum_core.requests.request", return_value=response), \
             patch.object(client, "_record_trace_log_solum") as record:
            result = client.request("GET", "common/labels/detail?label=X", company="962")
        self.assertEqual(result["status"], "SUCCESS")
        record.assert_called()
        kwargs = record.call_args.kwargs
        self.assertEqual(kwargs["solum_trace_id"], "cli-aabbccddee11")
        self.assertEqual(kwargs["status_code"], 200)

    def test_request_exception_keeps_null_trace_id(self):
        import requests

        client = SolumCoreClient(trace_id="cli-aabbccddee11")
        with patch.object(client, "_ensure_valid_token", return_value="tok"), \
             patch.object(client, "_log_curl_debug"), \
             patch(
                 "services.solum_core.requests.request",
                 side_effect=requests.exceptions.Timeout("timed out"),
             ), \
             patch.object(client, "_record_trace_log_solum") as record:
            with self.assertRaises(SolumAPIError):
                client.request("GET", "common/labels/detail", company="962")
        kwargs = record.call_args.kwargs
        self.assertIsNone(kwargs.get("solum_trace_id"))
        self.assertIn("timed out", kwargs.get("error", ""))


if __name__ == "__main__":
    unittest.main()
