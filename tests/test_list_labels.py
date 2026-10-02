import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from unittest.mock import MagicMock, patch

SRC_DIR = os.path.join(os.path.dirname(__file__), "..", "src")
sys.path.insert(0, os.path.abspath(SRC_DIR))

from commands import list_labels


class FakeDB:
    def __init__(self, responses):
        self.responses = list(responses)
        self.queries = []

    def ejecutar_consulta(self, query, params=None):
        self.queries.append((query, params))
        if not self.responses:
            return []
        return self.responses.pop(0)


class ListLabelsCommandTests(unittest.TestCase):
    def test_missing_args_prints_usage(self):
        db = FakeDB([])
        buf = io.StringIO()
        with redirect_stdout(buf):
            list_labels.execute(db, "cli-test", [])
        out = buf.getvalue()
        self.assertIn("Usage: list-labels", out)
        self.assertIn("[active|inactive|all] [yes|no] [label_name]", out)
        self.assertIn("[label_name]", out)
        self.assertIn("etiqueta", out)
        self.assertIn("P9", out)

    def test_local_listing_without_solum(self):
        db = FakeDB([
            [
                {"id": 2, "etiqueta": "Beta", "mac": "AA:BB", "activo": 0},
                {"id": 1, "etiqueta": "Alpha", "mac": "CC:DD", "activo": 1},
            ]
        ])
        buf = io.StringIO()
        with redirect_stdout(buf):
            list_labels.execute(db, "cli-test", ["madrid"])
        out = buf.getvalue()
        self.assertIn("Alpha", out)
        self.assertIn("Beta", out)
        self.assertIn("Total labels found: 2", out)
        self.assertNotIn("STATUS AT", out)
        self.assertIn("bd_feeltourist_madrid", db.queries[0][0])
        self.assertIn("dispositivos_etiquetas", db.queries[0][0])
        self.assertNotIn("etiqueta LIKE", db.queries[0][0])

    def _labels_sql(self, db):
        for query, params in db.queries:
            if "dispositivos_etiquetas" in query:
                return query, params
        self.fail("No dispositivos_etiquetas query was executed")

    def test_label_name_filter_sql_without_solum(self):
        db = FakeDB([
            [{"id": 1, "etiqueta": "P9", "mac": "AA:BB", "activo": 1}],
        ])
        buf = io.StringIO()
        with redirect_stdout(buf):
            list_labels.execute(db, "cli-test", ["madrid", "P9"])
        query, params = self._labels_sql(db)
        self.assertIn("WHERE", query)
        self.assertIn("etiqueta LIKE %s", query)
        self.assertNotIn("activo = %s", query)
        self.assertEqual(params, ("%P9%",))
        self.assertIn("P9", buf.getvalue())
        self.assertNotIn("STATUS AT", buf.getvalue())
        self.assertIn("[P9]", buf.getvalue())

    def test_label_name_filter_with_solum_shortcut(self):
        db = FakeDB([
            [{"labels_user": "TWI", "labels_password": "1024"}],
            [{"id": 1, "etiqueta": "P9", "mac": "00:11:22:33:44:55", "activo": 1}],
        ])
        buf = io.StringIO()
        with patch("commands.list_labels.SolumLabelService") as svc_cls:
            svc = svc_cls.return_value
            svc.get_details.return_value = {
                "status": "SUCCESS",
                "status_at": "ON",
                "last_connection": "2026-09-09 14:15:11.466",
                "raw_data": {},
            }
            svc.get_type_info.return_value = {
                "status": "SUCCESS",
                "totalPage": "7",
                "nfc": "YES",
                "responseMessage": "SUCCESS",
                "raw_data": {},
            }
            with redirect_stdout(buf):
                list_labels.execute(db, "cli-test", ['"bd_twistic_demo"', "yes", "P9"])
        query, params = self._labels_sql(db)
        self.assertIn("`bd_twistic_demo`.dispositivos_etiquetas", query)
        self.assertIn("etiqueta LIKE %s", query)
        self.assertEqual(params, ("%P9%",))
        self.assertIn("STATUS AT", buf.getvalue())
        svc.get_details.assert_called_once()

    def test_label_name_filter_with_active_and_solum(self):
        db = FakeDB([
            [{"labels_user": "TWI", "labels_password": "1024"}],
            [{"id": 1, "etiqueta": "P9", "mac": "00:11:22:33:44:55", "activo": 1}],
        ])
        with patch("commands.list_labels.SolumLabelService") as svc_cls:
            svc = svc_cls.return_value
            svc.get_details.return_value = {
                "status": "SUCCESS",
                "status_at": "ON",
                "last_connection": "N/A",
                "raw_data": {},
            }
            svc.get_type_info.return_value = {
                "status": "SUCCESS",
                "totalPage": "1",
                "nfc": "NO",
                "responseMessage": "SUCCESS",
                "raw_data": {},
            }
            buf = io.StringIO()
            with redirect_stdout(buf):
                list_labels.execute(db, "cli-test", ["madrid", "active", "yes", "P9"])
        query, params = self._labels_sql(db)
        self.assertIn("activo = %s", query)
        self.assertIn("etiqueta LIKE %s", query)
        self.assertEqual(params, (1, "%P9%"))
        self.assertIn("[ACTIVE]", buf.getvalue())
        self.assertIn("[P9]", buf.getvalue())

    def test_active_filter_and_solum_enrichment(self):
        db = FakeDB([
            [{"labels_user": "TWI", "labels_password": "1024"}],
            [{"id": 1, "etiqueta": "Room-101", "mac": "00:11:22:33:44:55", "activo": 1}],
        ])
        buf = io.StringIO()
        with patch("commands.list_labels.SolumLabelService") as svc_cls:
            svc = svc_cls.return_value
            svc.get_details.return_value = {
                "status": "SUCCESS",
                "status_at": "ON",
                "last_connection": "2026-09-09 14:15:11.466",
                "raw_data": {
                    "labelCode": "0AF4D1C0B291",
                    "currentImage": [{"index": 1, "state": "SUCCESS", "statusUpdateTime": "2026-09-09 14:15:11.466"}],
                },
            }
            svc.get_type_info.return_value = {
                "status": "SUCCESS",
                "totalPage": "7",
                "nfc": "YES",
                "responseMessage": "SUCCESS",
                "raw_data": {
                    "labelCode": "03704160B297",
                    "name": "NEWTON_GRAPHIC_2_9_RED_NFC",
                    "totalPage": 7,
                    "nfc": True,
                    "responseMessage": "SUCCESS",
                },
            }
            with redirect_stdout(buf):
                list_labels.execute(db, "cli-test", ["madrid", "active", "yes"])
        out = buf.getvalue()
        self.assertIn("TOTAL PAGE", out)
        self.assertIn("NFC", out)
        self.assertIn("RESPONSE MESSAGE", out)
        self.assertNotIn("totalPage", out)
        self.assertNotIn("BATT", out)
        self.assertIn("Room-101", out)
        self.assertIn("14:15:11", out)
        self.assertIn("7", out)
        self.assertIn("YES", out)
        self.assertIn("SUCCESS", out)
        svc.get_details.assert_called_once()
        svc.get_type_info.assert_called_once()
        kwargs = svc.get_details.call_args.kwargs
        self.assertEqual(kwargs["company"], "TWI")
        self.assertEqual(kwargs["store"], "1024")

    def test_status_at_follows_detail_even_if_type_info_success(self):
        db = FakeDB([
            [{"labels_user": "TWI", "labels_password": "1024"}],
            [{"id": 1, "etiqueta": "A1", "mac": "00:11:22:33:44:55", "activo": 1}],
        ])
        buf = io.StringIO()
        with patch("commands.list_labels.SolumLabelService") as svc_cls:
            svc = svc_cls.return_value
            svc.get_details.return_value = {
                "status": "SUCCESS",
                "status_at": "OFF",
                "last_connection": "2026-09-17 06:07:34",
                "raw_data": {"currentImage": [{"index": 1, "state": "PROCESSING", "statusUpdateTime": "2026-09-17 06:07:34"}]},
            }
            svc.get_type_info.return_value = {
                "status": "SUCCESS",
                "totalPage": "7",
                "nfc": "YES",
                "responseMessage": "SUCCESS",
                "raw_data": {},
            }
            with redirect_stdout(buf):
                list_labels.execute(db, "cli-test", ["madrid", "active", "yes"])
        out = buf.getvalue()
        self.assertIn("A1", out)
        self.assertIn("SUCCESS", out)
        self.assertIn("🔴 OFF", out)
        self.assertEqual(out.count("🟢 ON"), 1)

    def test_status_at_timeout_when_detail_image_state_timeout(self):
        db = FakeDB([
            [{"labels_user": "TWI", "labels_password": "1024"}],
            [{"id": 1, "etiqueta": "P9", "mac": "07:58:89:2B:B6:96", "activo": 1}],
        ])
        buf = io.StringIO()
        with patch("commands.list_labels.SolumLabelService") as svc_cls:
            svc = svc_cls.return_value
            svc.get_details.return_value = {
                "status": "SUCCESS",
                "status_at": "TIMEOUT",
                "last_connection": "2025-10-09 17:37:50.815",
                "raw_data": {},
            }
            svc.get_type_info.return_value = {
                "status": "SUCCESS",
                "totalPage": "7",
                "nfc": "YES",
                "responseMessage": "SUCCESS",
                "raw_data": {},
            }
            with redirect_stdout(buf):
                list_labels.execute(db, "cli-test", ["madrid", "active", "yes"])
        out = buf.getvalue()
        self.assertIn("🟡 TIMEOUT", out)
        self.assertIn("17:37:50", out)
        self.assertNotIn("02:38:14", out)
        self.assertEqual(out.count("🟢 ON"), 1)
        self.assertIn("SUCCESS", out)

    def test_status_at_timeout_when_type_info_times_out(self):
        db = FakeDB([
            [{"labels_user": "TWI", "labels_password": "1024"}],
            [{"id": 1, "etiqueta": "X96", "mac": "00:11:22:33:44:55", "activo": 1}],
        ])
        buf = io.StringIO()
        with patch("commands.list_labels.SolumLabelService") as svc_cls:
            svc = svc_cls.return_value
            svc.get_details.return_value = {
                "status": "SUCCESS",
                "status_at": "OFF",
                "last_connection": "2026-09-17 06:07:34",
                "raw_data": {"currentImage": [{"index": 1, "state": "PROCESSING", "statusUpdateTime": "2026-09-17 06:07:34"}]},
            }
            svc.get_type_info.side_effect = Exception(
                "ConnectTimeoutError to eu.common.solumesl.com (connect timeout=10)"
            )
            with redirect_stdout(buf):
                list_labels.execute(db, "cli-test", ["madrid", "active", "yes"])
        out = buf.getvalue()
        self.assertIn("X96", out)
        self.assertIn("ERROR", out)
        self.assertIn("06:07:34", out)
        self.assertIn("🔴 OFF", out)
        self.assertNotIn("🟡 TIMEOUT", out)
        self.assertNotIn("[SOLUM TYPE INFO]", out)
        self.assertEqual(out.count("🟢 ON"), 1)

    def test_type_info_405_keeps_clean_table_row(self):
        db = FakeDB([
            [{"labels_user": "TWI", "labels_password": "1024"}],
            [{"id": 478, "etiqueta": "EXT82", "mac": "09:D9:65:B6:9E", "activo": 1}],
        ])
        buf = io.StringIO()
        with patch("commands.list_labels.SolumLabelService") as svc_cls:
            svc = svc_cls.return_value
            svc.get_details.return_value = {
                "status": "SUCCESS",
                "status_at": "ON",
                "last_connection": "N/A",
                "raw_data": {},
            }
            svc.get_type_info.side_effect = Exception(
                "405 Client Error: Method Not Allowed for url: "
                "https://eu.common.solumesl.com/common/api/v2/common/labels/type/info?company=TWI"
            )
            with redirect_stdout(buf):
                list_labels.execute(db, "cli-test", ["clubgrancanaria", "active", "yes"])
        out = buf.getvalue()
        self.assertIn("EXT82", out)
        self.assertIn("🟢 ON", out)
        self.assertIn("ERROR", out)
        self.assertNotIn("⚠️ ERROR", out)
        self.assertNotIn("[SOLUM TYPE INFO]", out)
        self.assertNotIn("Method Not Allowed", out)
        self.assertNotIn("eu.common.solumesl.com", out)

    def test_status_at_timeout_when_type_info_returns_timeout(self):
        db = FakeDB([
            [{"labels_user": "TWI", "labels_password": "1024"}],
            [{"id": 1, "etiqueta": "A1", "mac": "00:11:22:33:44:55", "activo": 1}],
        ])
        buf = io.StringIO()
        with patch("commands.list_labels.SolumLabelService") as svc_cls:
            svc = svc_cls.return_value
            svc.get_details.return_value = {
                "status": "SUCCESS",
                "status_at": "ON",
                "last_connection": "2026-09-17 06:07:34",
                "raw_data": {},
            }
            svc.get_type_info.return_value = {"status": "TIMEOUT", "totalPage": "N/A", "nfc": "N/A", "responseMessage": "N/A"}
            with redirect_stdout(buf):
                list_labels.execute(db, "cli-test", ["madrid", "active", "yes"])
        out = buf.getvalue()
        self.assertIn("🟢 ON", out)
        self.assertNotIn("🟡 TIMEOUT", out)
        self.assertEqual(out.count("🟢 ON"), 2)


if __name__ == "__main__":
    unittest.main()
