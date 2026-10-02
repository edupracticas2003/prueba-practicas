import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

SRC_DIR = os.path.join(os.path.dirname(__file__), "..", "src")
sys.path.insert(0, os.path.abspath(SRC_DIR))

from services.trace_log import (
    ORIGEN_APLICACION,
    ORIGEN_COMANDO,
    TraceLogService,
    adopt_request_trace_log,
    inbound_request_payload,
    sanitize_for_trace,
)


class FakeDB:
    def __init__(self, insert_id=42):
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

    def __exit__(self, exc_type, exc, tb):
        return False


class SanitizeForTraceTests(unittest.TestCase):
    def test_redacts_password_and_token_keys(self):
        raw = {
            "username": "admin",
            "password": "secret",
            "Token-Header": "abc",
            "nested": {"access_token": "xyz", "db_name": "madrid"},
        }
        clean = sanitize_for_trace(raw)
        self.assertEqual(clean["username"], "admin")
        self.assertEqual(clean["password"], "***")
        self.assertEqual(clean["Token-Header"], "***")
        self.assertEqual(clean["nested"]["access_token"], "***")
        self.assertEqual(clean["nested"]["db_name"], "madrid")

    def test_redacts_bearer_and_sas_in_strings(self):
        text = (
            "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.abc "
            "https://cdn.example/img.png?sv=2021&sig=DEADBEEF&se=2026"
        )
        clean = sanitize_for_trace(text)
        self.assertNotIn("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.abc", clean)
        self.assertIn("Bearer ***", clean)
        self.assertIn("sig=***", clean)
        self.assertNotIn("DEADBEEF", clean)

    def test_inbound_payload_strips_identity_keys(self):
        clean = inbound_request_payload(
            {
                "command": "list-labels",
                "args": ["madrid"],
                "trace_id": "cli-should-not-persist",
                "trace_id_solum": "also-no",
                "session_trace_id": "nope",
                "password": "secret",
            }
        )
        self.assertEqual(clean["command"], "list-labels")
        self.assertEqual(clean["args"], ["madrid"])
        self.assertEqual(clean["password"], "***")
        self.assertNotIn("trace_id", clean)
        self.assertNotIn("trace_id_solum", clean)
        self.assertNotIn("session_trace_id", clean)


class TraceLogServiceTests(unittest.TestCase):
    def tearDown(self):
        TraceLogService.clear_bind()
        TraceLogService.unregister_correlation("cli-session")
        TraceLogService.unregister_correlation("web-api-abc")
        TraceLogService.unregister_row(21)
        TraceLogService.unregister_row(55)

    @patch("services.trace_log.MySQLConnection")
    def test_start_inserts_comando_payload(self, mock_conn):
        db = FakeDB(insert_id=7)
        mock_conn.return_value = FakeConnection(db)
        svc = TraceLogService(loki_trace_id="cli-abc123")

        row_id = svc.start(
            ORIGEN_COMANDO,
            {
                "command": "list-labels",
                "args": ["madrid", "active", "yes"],
                "raw": "list-labels madrid active yes",
                "trace_id": "cli-must-not-appear-in-payload",
                "password": "should-not-persist",
            },
        )

        self.assertEqual(row_id, 7)
        mock_conn.assert_called_with(suffix="_COM", trace_id="cli-abc123")
        self.assertEqual(len(db.calls), 1)
        query, params = db.calls[0]
        self.assertIn("INSERT INTO tw_com_labels.trace_log", query)
        self.assertEqual(params[0], ORIGEN_COMANDO)
        payload = json.loads(params[1])
        self.assertEqual(payload["command"], "list-labels")
        self.assertEqual(payload["raw"], "list-labels madrid active yes")
        self.assertEqual(payload["password"], "***")
        self.assertNotIn("trace_id", payload)
        self.assertNotIn("trace_id_solum", payload)

    @patch("services.trace_log.MySQLConnection")
    def test_start_api_payload_is_http_request_only(self, mock_conn):
        db = FakeDB(insert_id=11)
        mock_conn.return_value = FakeConnection(db)
        svc = TraceLogService(loki_trace_id="api-/api/v1/labels/query-status")
        row_id = svc.start(
            ORIGEN_APLICACION,
            {
                "method": "POST",
                "path": "/api/v1/labels/query-status",
                "query": {},
                "body": {"db_name": "madrid", "label_status": True},
                "trace_id": "web-api-should-not-land-here",
            },
        )
        self.assertEqual(row_id, 11)
        payload = json.loads(db.calls[0][1][1])
        self.assertEqual(payload["method"], "POST")
        self.assertEqual(payload["path"], "/api/v1/labels/query-status")
        self.assertEqual(payload["body"]["db_name"], "madrid")
        self.assertNotIn("trace_id", payload)

    @patch("services.trace_log.MySQLConnection")
    def test_start_fail_soft_returns_none(self, mock_conn):
        mock_conn.side_effect = RuntimeError("db down")
        svc = TraceLogService(loki_trace_id="cli-x")
        self.assertIsNone(svc.start(ORIGEN_APLICACION, {"method": "POST"}))

    @patch("services.trace_log.MySQLConnection")
    def test_begin_binds_and_clear(self, mock_conn):
        db = FakeDB(insert_id=9)
        mock_conn.return_value = FakeConnection(db)
        svc = TraceLogService(loki_trace_id="web-api-1")
        self.assertIsNone(TraceLogService.current_row_id())
        self.assertEqual(svc.begin(ORIGEN_APLICACION, {"path": "/api/v1/labels/query-status"}), 9)
        self.assertEqual(TraceLogService.current_row_id(), 9)
        svc.clear()
        self.assertIsNone(TraceLogService.current_row_id())

    @patch("services.trace_log.MySQLConnection")
    def test_solum_contact_always_updates_even_without_id(self, mock_conn):
        """Tras Solum, UPDATE siempre (id o NULL) y fuerza ``updated``."""
        db = FakeDB()
        mock_conn.return_value = FakeConnection(db)
        TraceLogService.bind(55)
        ok = TraceLogService.mark_after_solum_current(
            trace_id_solum=None,
            solum_meta={"method": "GET", "endpoint": "common/labels/detail", "status_code": 200},
            loki_trace_id="cli-session",
            correlation_id="cli-session",
        )
        self.assertTrue(ok)
        self.assertGreaterEqual(len(db.calls), 1)
        update_query, update_params = db.calls[0]
        self.assertIn("UPDATE tw_com_labels.trace_log", update_query)
        self.assertIn("updated = CURRENT_TIMESTAMP", update_query)
        self.assertNotIn("updated_at", update_query)
        self.assertEqual(update_params, (None, 55))

    @patch("services.trace_log.MySQLConnection")
    def test_solum_id_written_when_present(self, mock_conn):
        db = FakeDB()
        mock_conn.return_value = FakeConnection(db)
        TraceLogService.bind(55)
        ok = TraceLogService.mark_after_solum_current(
            trace_id_solum="solum-real-trace-99",
            solum_meta={
                "method": "GET",
                "endpoint": "common/labels/detail",
                "status_code": 200,
                "trace_id": "must-not-enter-solum-calls",
            },
            loki_trace_id="cli-deadbeef12",
        )
        self.assertTrue(ok)
        update_query, update_params = db.calls[0]
        self.assertIn("trace_id_solum", update_query)
        self.assertEqual(update_params, ("solum-real-trace-99", 55))
        self.assertEqual(len(db.calls), 2)
        meta = json.loads(db.calls[1][1][0])
        self.assertEqual(meta["endpoint"], "common/labels/detail")
        self.assertNotIn("trace_id", meta)

    @patch("services.trace_log.MySQLConnection")
    def test_fail_before_solum_keeps_null_no_update(self, mock_conn):
        """INSERT ok; sin contacto Solum → ningún UPDATE (trace_id_solum NULL)."""
        db = FakeDB(insert_id=21)
        mock_conn.return_value = FakeConnection(db)
        svc = TraceLogService(loki_trace_id="cli-session")
        row_id = svc.begin(
            ORIGEN_COMANDO,
            {"command": "list-labels", "args": ["madrid"], "raw": "list-labels madrid"},
            correlation_id="cli-session",
        )
        self.assertEqual(row_id, 21)
        payload = json.loads(db.calls[0][1][1])
        self.assertEqual(payload["command"], "list-labels")
        self.assertNotIn("trace_id", payload)
        self.assertEqual(len(db.calls), 1)
        self.assertTrue(db.calls[0][0].strip().upper().startswith("INSERT"))
        svc.clear()

    @patch("services.trace_log.MySQLConnection")
    def test_correlation_resolves_when_contextvar_empty(self, mock_conn):
        db = FakeDB()
        mock_conn.return_value = FakeConnection(db)
        TraceLogService.clear_bind()
        TraceLogService.register_correlation("cli-session", 77)
        ok = TraceLogService.mark_after_solum_current(
            trace_id_solum=None,
            correlation_id="cli-session",
            solum_meta={"method": "GET", "endpoint": "common/labels/detail"},
        )
        self.assertTrue(ok)
        self.assertEqual(db.calls[0][1], (None, 77))
        TraceLogService.unregister_correlation("cli-session")

    def test_adopt_request_trace_log(self):
        request = MagicMock()
        request.state.trace_log_row_id = 88
        TraceLogService.clear_bind()
        row_id = adopt_request_trace_log(request, "web-api-abc")
        self.assertEqual(row_id, 88)
        self.assertEqual(TraceLogService.current_row_id(), 88)
        self.assertEqual(TraceLogService.resolve_row_id(correlation_id="web-api-abc"), 88)
        TraceLogService.unregister_correlation("web-api-abc")
        TraceLogService.clear_bind()

    @patch("services.trace_log.MySQLConnection")
    def test_attach_fail_soft(self, mock_conn):
        mock_conn.side_effect = RuntimeError("write failed")
        TraceLogService.bind(3)
        self.assertFalse(
            TraceLogService(loki_trace_id="x").mark_after_solum("solum-x", row_id=3)
        )


if __name__ == "__main__":
    unittest.main()
