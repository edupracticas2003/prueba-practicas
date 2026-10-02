import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from unittest.mock import MagicMock, patch

SRC_DIR = os.path.join(os.path.dirname(__file__), "..", "src")
sys.path.insert(0, os.path.abspath(SRC_DIR))

from fastapi import HTTPException
from routers.labels import LabelQueryRequest, api_query_labels_status


def _request():
    req = MagicMock()
    req.state.trace_log_row_id = 1
    return req


class FakeDB:
    def __init__(self, responses):
        self.responses = list(responses)

    def ejecutar_consulta(self, query, params=None):
        if not self.responses:
            return []
        return self.responses.pop(0)


class FakeConnection:
    def __init__(self, db):
        self.db = db

    def __enter__(self):
        return self.db

    def __exit__(self, exc_type, exc, tb):
        return False


class LabelsRouterTests(unittest.TestCase):
    def test_query_local_labels(self):
        db = FakeDB([
            [{"id": 1, "etiqueta": "Room-101", "mac": "00:11:22:33:44:55", "activo": 1}],
        ])
        payload = LabelQueryRequest(db_name="madrid", label_status=True, solum_status=False)
        with patch("routers.labels.MySQLConnection", return_value=FakeConnection(db)):
            result = api_query_labels_status(payload, _request(), token="tok")

        self.assertTrue(result["success"])
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["labels"][0]["etiqueta"], "Room-101")
        self.assertEqual(result["labels"][0]["status_tw"], "ON")
        self.assertEqual(result["labels"][0]["status_at"], "UNKNOWN")
        self.assertEqual(result["labels"][0]["db_name"], "madrid")

    def test_query_with_solum_status(self):
        db = FakeDB([
            [{"labels_user": "TWI", "labels_password": "1024"}],
            [{"id": 1, "etiqueta": "Room-101", "mac": "00:11:22:33:44:55", "activo": 1}],
        ])
        payload = LabelQueryRequest(db_name="madrid", label_status=True, solum_status=True)
        with patch("routers.labels.MySQLConnection", return_value=FakeConnection(db)), \
             patch("routers.labels.SolumLabelService") as svc_cls:
            svc_cls.return_value.get_details.return_value = {
                "status": "SUCCESS",
                "status_at": "ON",
                "last_connection": "2026-09-09 14:15:11.466",
                "raw_data": {
                    "labelCode": "0AF4D1C0B291",
                    "currentImage": [{"index": 1, "state": "SUCCESS", "statusUpdateTime": "2026-09-09 14:15:11.466"}],
                },
            }
            svc_cls.return_value.get_type_info.return_value = {
                "status": "SUCCESS",
                "totalPage": "7",
                "nfc": "YES",
                "responseMessage": "SUCCESS",
                "raw_data": {"totalPage": 7, "nfc": True, "responseMessage": "SUCCESS"},
            }
            result = api_query_labels_status(payload, _request(), token="tok")

        row = result["labels"][0]
        self.assertEqual(row["status_at"], "ON")
        self.assertEqual(row["totalPage"], "7")
        self.assertEqual(row["nfc"], "YES")
        self.assertEqual(row["responseMessage"], "SUCCESS")
        self.assertNotIn("battery", row)
        self.assertNotIn("firmware", row)
        self.assertEqual(row["last_connection"], "2026-09-09 14:15:11.466")

    def test_status_at_follows_detail_not_type_info(self):
        db = FakeDB([
            [{"labels_user": "TWI", "labels_password": "1024"}],
            [{"id": 1, "etiqueta": "A1", "mac": "00:11:22:33:44:55", "activo": 1}],
        ])
        payload = LabelQueryRequest(db_name="madrid", label_status=True, solum_status=True)
        with patch("routers.labels.MySQLConnection", return_value=FakeConnection(db)), \
             patch("routers.labels.SolumLabelService") as svc_cls:
            svc_cls.return_value.get_details.return_value = {
                "status": "SUCCESS",
                "status_at": "OFF",
                "last_connection": "2026-09-17 06:07:34",
                "raw_data": {},
            }
            svc_cls.return_value.get_type_info.return_value = {
                "status": "SUCCESS",
                "totalPage": "7",
                "nfc": "YES",
                "responseMessage": "SUCCESS",
                "raw_data": {},
            }
            result = api_query_labels_status(payload, _request(), token="tok")
        row = result["labels"][0]
        self.assertEqual(row["status_tw"], "ON")
        self.assertEqual(row["status_at"], "OFF")
        self.assertEqual(row["responseMessage"], "SUCCESS")

    def test_status_at_not_on_when_type_info_fails(self):
        db = FakeDB([
            [{"labels_user": "TWI", "labels_password": "1024"}],
            [{"id": 1, "etiqueta": "A1", "mac": "00:11:22:33:44:55", "activo": 1}],
        ])
        payload = LabelQueryRequest(db_name="madrid", label_status=True, solum_status=True)
        with patch("routers.labels.MySQLConnection", return_value=FakeConnection(db)), \
             patch("routers.labels.SolumLabelService") as svc_cls:
            svc_cls.return_value.get_details.return_value = {
                "status": "SUCCESS",
                "status_at": "ON",
                "last_connection": "2026-09-17 06:07:34",
                "raw_data": {},
            }
            svc_cls.return_value.get_type_info.side_effect = Exception("ConnectTimeout")
            result = api_query_labels_status(payload, _request(), token="tok")
        row = result["labels"][0]
        self.assertEqual(row["status_tw"], "ON")
        self.assertEqual(row["status_at"], "ON")
        self.assertEqual(row["totalPage"], "ERROR")
        self.assertEqual(row["nfc"], "ERROR")
        self.assertEqual(row["responseMessage"], "ERROR")
        self.assertEqual(row["last_connection"], "2026-09-17 06:07:34")

    def test_status_at_error_when_type_info_fails_without_timeout(self):
        db = FakeDB([
            [{"labels_user": "TWI", "labels_password": "1024"}],
            [{"id": 1, "etiqueta": "A1", "mac": "00:11:22:33:44:55", "activo": 1}],
        ])
        payload = LabelQueryRequest(db_name="madrid", label_status=True, solum_status=True)
        with patch("routers.labels.MySQLConnection", return_value=FakeConnection(db)), \
             patch("routers.labels.SolumLabelService") as svc_cls:
            svc_cls.return_value.get_details.return_value = {
                "status": "SUCCESS",
                "status_at": "ON",
                "last_connection": "2026-09-17 06:07:34",
                "raw_data": {},
            }
            svc_cls.return_value.get_type_info.side_effect = Exception("HTTP 500 Internal Server Error")
            result = api_query_labels_status(payload, _request(), token="tok")
        row = result["labels"][0]
        self.assertEqual(row["status_tw"], "ON")
        self.assertEqual(row["status_at"], "ON")
        self.assertEqual(row["responseMessage"], "ERROR")

    def test_type_info_failure_does_not_print_to_stdout(self):
        db = FakeDB([
            [{"labels_user": "TWI", "labels_password": "1024"}],
            [{"id": 1, "etiqueta": "EXT82", "mac": "09D965B69E", "activo": 1}],
        ])
        payload = LabelQueryRequest(db_name="madrid", label_status=True, solum_status=True)
        buf = io.StringIO()
        with patch("routers.labels.MySQLConnection", return_value=FakeConnection(db)), \
             patch("routers.labels.SolumLabelService") as svc_cls:
            svc_cls.return_value.get_details.return_value = {
                "status": "SUCCESS",
                "status_at": "ON",
                "last_connection": "N/A",
                "raw_data": {},
            }
            svc_cls.return_value.get_type_info.side_effect = Exception(
                "405 Client Error: Method Not Allowed for url: https://eu.common.solumesl.com/common/api/v2/common/labels/type/info"
            )
            with redirect_stdout(buf):
                result = api_query_labels_status(payload, _request(), token="tok")
        row = result["labels"][0]
        self.assertEqual(row["status_at"], "ON")
        self.assertNotIn("[SOLUM TYPE INFO]", buf.getvalue())
        self.assertNotIn("Method Not Allowed", buf.getvalue())

    def test_not_found_when_empty(self):
        db = FakeDB([[]])
        payload = LabelQueryRequest(db_name="madrid", label_status=True, solum_status=False)
        with patch("routers.labels.MySQLConnection", return_value=FakeConnection(db)):
            with self.assertRaises(HTTPException) as ctx:
                api_query_labels_status(payload, _request(), token="tok")
        self.assertEqual(ctx.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
