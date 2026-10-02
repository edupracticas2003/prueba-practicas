import os
import sys
import unittest
from unittest.mock import MagicMock, patch

SRC_DIR = os.path.join(os.path.dirname(__file__), "..", "src")
sys.path.insert(0, os.path.abspath(SRC_DIR))

from database import MySQLConnection


class MySQLConnectionFreshReadTests(unittest.TestCase):
    def _open(self, autocommit=True):
        conn = MagicMock()
        conn.is_connected.return_value = True
        conn.autocommit = autocommit
        cursor = MagicMock()
        cursor.fetchall.return_value = [{"id": 1, "etiqueta": "P9"}]
        conn.cursor.return_value = cursor
        return conn, cursor

    @patch("database.mysql.connector.connect")
    def test_connect_enables_autocommit(self, mock_connect):
        conn, _cursor = self._open()
        mock_connect.return_value = conn
        with MySQLConnection(suffix="_DATA", trace_id="cli-test") as db:
            self.assertIs(db.conexion, conn)
        kwargs = mock_connect.call_args.kwargs
        self.assertTrue(kwargs.get("autocommit"))
        self.assertTrue(conn.autocommit)

    @patch("database.mysql.connector.connect")
    def test_select_rollbacks_before_read(self, mock_connect):
        conn, cursor = self._open(autocommit=False)
        mock_connect.return_value = conn
        with MySQLConnection(suffix="_DATA", trace_id="cli-test") as db:
            rows = db.ejecutar_consulta(
                "SELECT id, etiqueta, mac, activo FROM dispositivos_etiquetas"
            )
        self.assertEqual(rows, [{"id": 1, "etiqueta": "P9"}])
        conn.rollback.assert_called()
        cursor.execute.assert_called()

    @patch("database.mysql.connector.connect")
    def test_select_pings_before_read(self, mock_connect):
        conn, _cursor = self._open()
        mock_connect.return_value = conn
        with MySQLConnection(suffix="_DATA", trace_id="cli-test") as db:
            db.ejecutar_consulta("SELECT 1")
        conn.ping.assert_called()


if __name__ == "__main__":
    unittest.main()
