"""End-to-end: INSERT payload sin trace_id; tras Solum OK → columna trace_id_solum."""
import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

SRC_DIR = os.path.join(os.path.dirname(__file__), "..", "src")
sys.path.insert(0, os.path.abspath(SRC_DIR))

from services.solum_core import SolumCoreClient
from services.trace_log import (
    ORIGEN_APLICACION,
    ORIGEN_COMANDO,
    TraceLogService,
    adopt_request_trace_log,
    inbound_request_payload,
)

AIMS_OK = {
    "labelCode": "0AF4D1C0B291",
    "responseCode": "200",
    "responseMessage": "OK",
}


class FakeDB:
    def __init__(self, insert_id=100):
        self.insert_id = insert_id
        self.calls = []

    def ejecutar_consulta(self, query, params=None):
        self.calls.append((query.strip(), params))
        if query.strip().upper().startswith("INSERT"):
            return self.insert_id
        return []


class FakeConnection:
    def __init__(self, db):
        self.db = db

    def __enter__(self):
        return self.db

    def __exit__(self, *a):
        return False


class TraceLogE2ETests(unittest.TestCase):
    def tearDown(self):
        TraceLogService.clear_bind()
        TraceLogService.unregister_correlation("cli-f72ca1131b55")
        TraceLogService.unregister_correlation("web-api-abcdef123456")
        TraceLogService.unregister_row(100)
        TraceLogService.unregister_row(200)
        SolumCoreClient._tokens = {"TWI": None, "962": None}
        SolumCoreClient._tokens_expires_at = {"TWI": 0.0, "962": 0.0}

    def _aims_response(self, status=200, body=None):
        response = MagicMock()
        response.status_code = status
        response.headers = {"Content-Type": "application/json"}
        payload = body if body is not None else AIMS_OK
        response.text = json.dumps(payload)
        response.json.return_value = dict(payload)
        response.raise_for_status = MagicMock()
        return response

    @patch("services.trace_log.MySQLConnection")
    def test_cli_show_label_flow_writes_correlation_to_column(self, mock_conn):
        """Replica el caso real: show-label con cli-f72ca1131b55 → columna, no payload."""
        db = FakeDB(insert_id=100)
        mock_conn.return_value = FakeConnection(db)
        flow_id = "cli-f72ca1131b55"

        svc = TraceLogService(loki_trace_id=flow_id)
        row_id = svc.begin(
            ORIGEN_COMANDO,
            {
                "command": "show-label",
                "args": ['"bd_twistic_demo"', "JC1"],
                "raw": 'show-label "bd_twistic_demo" JC1',
                # si alguien lo pasa por error, debe striparse del INSERT
                "trace_id": flow_id,
            },
            correlation_id=flow_id,
        )
        self.assertEqual(row_id, 100)
        insert_payload = json.loads(db.calls[0][1][1])
        self.assertEqual(insert_payload["command"], "show-label")
        self.assertNotIn("trace_id", insert_payload)

        client = SolumCoreClient(trace_id=flow_id)
        with patch.object(client, "_ensure_valid_token", return_value="tok"), \
             patch.object(client, "_log_curl_debug"), \
             patch("services.solum_core.requests.request", return_value=self._aims_response()):
            result = client.request(
                "GET",
                "common/labels/detail?company=TWI&store=1024&label=JC1",
                company="TWI",
            )
        self.assertEqual(result["status"], "SUCCESS")

        updates = [c for c in db.calls if c[0].upper().startswith("UPDATE")]
        self.assertTrue(updates)
        first_update_q, first_update_p = updates[0]
        self.assertIn("trace_id_solum", first_update_q)
        self.assertIn("updated = CURRENT_TIMESTAMP", first_update_q)
        self.assertNotIn("updated_at", first_update_q)
        self.assertEqual(first_update_p, (flow_id, 100))

        svc.clear()

    @patch("services.trace_log.MySQLConnection")
    def test_api_flow_writes_web_api_correlation(self, mock_conn):
        db = FakeDB(insert_id=200)
        mock_conn.return_value = FakeConnection(db)
        flow_id = "web-api-abcdef123456"

        # Middleware INSERT (sin correlation aún) + request.state
        mw = TraceLogService(loki_trace_id="api-/api/v1/labels/query-status")
        row_id = mw.begin(
            ORIGEN_APLICACION,
            {
                "method": "POST",
                "path": "/api/v1/labels/query-status",
                "body": {"db_name": "madrid", "solum_status": True},
            },
        )
        self.assertEqual(row_id, 200)
        insert_payload = json.loads(db.calls[0][1][1])
        self.assertNotIn("trace_id", insert_payload)

        # Handler sync re-bind + correlation (threadpool-safe)
        request = MagicMock()
        request.state.trace_log_row_id = row_id
        TraceLogService.clear_bind()  # simula pérdida de ContextVar del middleware
        adopt_request_trace_log(request, flow_id)

        client = SolumCoreClient(trace_id=flow_id)
        with patch.dict(os.environ, {"INCOMING_API_TOKEN": "org-tok"}), \
             patch("services.auth_service.AuthService.validar_token", return_value=True), \
             patch.object(client, "_ensure_valid_token", return_value="tok"), \
             patch.object(client, "_log_curl_debug"), \
             patch("services.solum_core.requests.request", return_value=self._aims_response()):
            client.request("GET", "common/labels/detail?label=X", company="TWI")

        updates = [c for c in db.calls if c[0].upper().startswith("UPDATE")]
        self.assertTrue(updates)
        self.assertEqual(updates[0][1], (flow_id, 200))
        mw.clear()
        TraceLogService.unregister_row(row_id)

    @patch("services.trace_log.MySQLConnection")
    def test_timeout_keeps_null_trace_id_solum(self, mock_conn):
        import requests

        db = FakeDB(insert_id=100)
        mock_conn.return_value = FakeConnection(db)
        flow_id = "cli-f72ca1131b55"
        svc = TraceLogService(loki_trace_id=flow_id)
        svc.begin(
            ORIGEN_COMANDO,
            {"command": "show-label", "args": ["demo", "JC1"], "raw": "show-label demo JC1"},
            correlation_id=flow_id,
        )
        client = SolumCoreClient(trace_id=flow_id)
        with patch.object(client, "_ensure_valid_token", return_value="tok"), \
             patch.object(client, "_log_curl_debug"), \
             patch(
                 "services.solum_core.requests.request",
                 side_effect=requests.exceptions.Timeout("timed out"),
             ):
            with self.assertRaises(Exception):
                client.request("GET", "common/labels/detail", company="TWI")

        updates = [c for c in db.calls if c[0].upper().startswith("UPDATE")]
        self.assertTrue(updates)
        self.assertEqual(updates[0][1][0], None)  # trace_id_solum NULL
        self.assertEqual(updates[0][1][1], 100)
        svc.clear()

    def test_inbound_payload_matches_user_example_without_trace_id(self):
        clean = inbound_request_payload(
            {
                "raw": 'show-label "bd_twistic_demo" JC1',
                "args": ['"bd_twistic_demo"', "JC1"],
                "command": "show-label",
                "trace_id": "cli-f72ca1131b55",
            }
        )
        self.assertEqual(
            clean,
            {
                "raw": 'show-label "bd_twistic_demo" JC1',
                "args": ['"bd_twistic_demo"', "JC1"],
                "command": "show-label",
            },
        )


if __name__ == "__main__":
    unittest.main()
