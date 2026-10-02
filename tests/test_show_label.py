# tests/test_show_label.py
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

SRC_DIR = os.path.join(os.path.dirname(__file__), "..", "src")
sys.path.insert(0, os.path.abspath(SRC_DIR))

from commands import show_label
from services.solum_labels import SolumLabelService
from services import label_image_viewer as viewer

# Fixture without real Azure SAS secrets — placeholder host only.
DETAIL_WITH_CONTENT = {
    "labelCode": "001122334455",
    "activePage": 2,
    "width": 384,
    "height": 168,
    "previousImage": [
        {
            "index": 2,
            "state": "SUCCESS",
            "processUpdateTime": "2026-09-08 10:00:00.000",
            "statusUpdateTime": "2026-09-08 10:01:00.000",
            "content": "https://example.invalid/prev2.png?sig=fake",
        },
    ],
    "currentImage": [
        {
            "index": 1,
            "state": "SUCCESS",
            "processUpdateTime": "2025-10-09 17:30:00.000",
            "statusUpdateTime": "2025-10-09 17:37:50.815",
            "content": "https://example.invalid/page1.png?sig=fake",
        },
        {
            "index": 2,
            "state": "SUCCESS",
            "processUpdateTime": "2026-09-09 14:14:40.000",
            "statusUpdateTime": "2026-09-09 14:15:11.466",
            "content": "https://example.invalid/page2.png?sig=fake",
        },
    ],
    "responseCode": "200",
    "responseMessage": "OK",
}


class FakeDB:
    def __init__(self, responses):
        self.responses = list(responses)
        self.queries = []

    def ejecutar_consulta(self, query, params=None):
        self.queries.append((query, params))
        if not self.responses:
            return []
        return self.responses.pop(0)


class ResolveDisplayImageTests(unittest.TestCase):
    def test_active_page_content_preferred(self):
        service = SolumLabelService(trace_id="cli-test")
        info = service.resolve_display_image(DETAIL_WITH_CONTENT)
        self.assertEqual(info["page"], 2)
        self.assertEqual(info["state"], "SUCCESS")
        self.assertEqual(info["status_at"], "ON")
        self.assertEqual(info["completed_date"], "2026-09-09 14:15:11.466")
        self.assertEqual(info["request_date"], "2026-09-09 14:14:40.000")
        self.assertEqual(info["last_connection"], "2026-09-09 14:15:11.466")
        self.assertEqual(info["content_url"], "https://example.invalid/page2.png?sig=fake")
        self.assertEqual(info["role"], "current")

    def test_previous_image_dates_and_content(self):
        service = SolumLabelService(trace_id="cli-test")
        info = service.resolve_display_image(DETAIL_WITH_CONTENT, source_key="previousImage")
        self.assertEqual(info["role"], "previous")
        self.assertEqual(info["page"], 2)
        self.assertEqual(info["completed_date"], "2026-09-08 10:01:00.000")
        self.assertEqual(info["request_date"], "2026-09-08 10:00:00.000")
        self.assertEqual(info["content_url"], "https://example.invalid/prev2.png?sig=fake")

    def test_resolve_show_images_both(self):
        service = SolumLabelService(trace_id="cli-test")
        both = service.resolve_show_images(DETAIL_WITH_CONTENT)
        self.assertEqual(both["current"]["role"], "current")
        self.assertEqual(both["previous"]["role"], "previous")
        self.assertIn("page2.png", both["current"]["content_url"])
        self.assertIn("prev2.png", both["previous"]["content_url"])

    def test_falls_back_to_first_content_when_active_missing_url(self):
        raw = {
            "activePage": 1,
            "currentImage": [
                {"index": 1, "state": "SUCCESS", "statusUpdateTime": "2025-01-01 00:00:00"},
                {
                    "index": 2,
                    "state": "SUCCESS",
                    "statusUpdateTime": "2025-01-02 00:00:00",
                    "processUpdateTime": "2025-01-01 23:00:00",
                    "content": "https://example.invalid/only.png?sig=fake",
                },
            ],
        }
        service = SolumLabelService(trace_id="cli-test")
        info = service.resolve_display_image(raw)
        self.assertEqual(info["page"], 2)
        self.assertEqual(info["content_url"], "https://example.invalid/only.png?sig=fake")
        self.assertEqual(info["request_date"], "2025-01-01 23:00:00")

    def test_no_images_unknown(self):
        service = SolumLabelService(trace_id="cli-test")
        info = service.resolve_display_image({"responseCode": "200"})
        self.assertIsNone(info["content_url"])
        self.assertEqual(info["status_at"], "UNKNOWN")
        self.assertEqual(info["completed_date"], "N/A")
        self.assertEqual(info["request_date"], "N/A")


class ShowLabelCommandTests(unittest.TestCase):
    def test_missing_args_prints_usage(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            show_label.execute(FakeDB([]), "cli-test", [])
        out = buf.getvalue()
        self.assertIn("Usage: show-label", out)
        self.assertIn("SHOW_LABEL_IMAGE_DIR", out)
        self.assertIn("SHOW_LABEL_HTTP_PORT", out)
        self.assertIn("SHOW_LABEL_PUBLIC_URL", out)
        self.assertIn("OSC-8", out)
        self.assertIn("previous", out.lower())

    def test_no_solum_cfg(self):
        db = FakeDB([[]])
        buf = io.StringIO()
        with redirect_stdout(buf):
            show_label.execute(db, "cli-test", ["madrid", "P9"])
        self.assertIn("Missing Solum company/store", buf.getvalue())

    def test_multiple_matches_asks_for_mac(self):
        db = FakeDB([
            [{"labels_user": "TWI", "labels_password": "1024"}],
            [
                {"id": 1, "etiqueta": "P9-A", "mac": "AA:BB:CC:DD:EE:01", "activo": 1},
                {"id": 2, "etiqueta": "P9-B", "mac": "AA:BB:CC:DD:EE:02", "activo": 1},
            ],
        ])
        buf = io.StringIO()
        with redirect_stdout(buf):
            show_label.execute(db, "cli-test", ["madrid", "P9"])
        out = buf.getvalue()
        self.assertIn("Multiple labels matched", out)
        self.assertIn("P9-A", out)
        self.assertIn("exact MAC", out)

    def test_show_label_html_viewer_and_both_images(self):
        db = FakeDB([
            [{"labels_user": "TWI", "labels_password": "1024"}],
            [{"id": 1, "etiqueta": "P9", "mac": "00:11:22:33:44:55", "activo": 1}],
        ])
        png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
        service = SolumLabelService(trace_id="cli-test")
        both = service.resolve_show_images(DETAIL_WITH_CONTENT)

        with tempfile.TemporaryDirectory() as tmp:
            env = {
                "SHOW_LABEL_IMAGE_DIR": tmp,
                "SHOW_LABEL_HTTP_PORT": "18765",
            }
            with patch.dict(os.environ, env, clear=False):
                os.environ.pop("SHOW_LABEL_PUBLIC_URL", None)
                os.environ.pop("SHOW_LABEL_HOST", None)
                with patch("commands.show_label.SolumLabelService") as svc_cls:
                    svc = svc_cls.return_value
                    svc.get_details.return_value = {
                        "status": "SUCCESS",
                        "status_at": "ON",
                        "last_connection": "2026-09-09 14:15:11.466",
                        "raw_data": DETAIL_WITH_CONTENT,
                    }
                    svc.resolve_show_images.return_value = both

                    mock_resp = MagicMock()
                    mock_resp.raise_for_status = MagicMock()
                    mock_resp.headers = {"Content-Type": "image/png"}
                    mock_resp.iter_content = MagicMock(return_value=[png_bytes])

                    with patch("services.label_image_viewer.requests.get", return_value=mock_resp):
                        with patch("services.label_image_viewer.has_display", return_value=False):
                            with patch("services.label_image_viewer.running_in_docker", return_value=True):
                                with patch("services.label_image_viewer.open_with_viewer", return_value=False):
                                    buf = io.StringIO()
                                    with redirect_stdout(buf):
                                        show_label.execute(db, "cli-test", ['"bd_twistic_demo"', "P9"])
                                    out = buf.getvalue()

            self.assertIn("LABEL:", out)
            self.assertIn("P9", out)
            self.assertIn("CURRENT IMAGE", out)
            self.assertIn("PREVIOUS IMAGE", out)
            self.assertIn("COMPLETED DATE:", out)
            self.assertIn("REQUEST DATE:", out)
            self.assertIn("2026-09-09 14:15:11.466", out)
            self.assertIn("2026-09-09 14:14:40.000", out)
            self.assertIn("2026-09-08 10:01:00.000", out)
            self.assertIn("ROOT LINK:", out)
            self.assertIn("http://127.0.0.1:18765/", out)
            self.assertIn("\033]8;;", out)  # OSC-8 on ROOT LINK
            self.assertNotIn("VIEWER LINK:", out)
            self.assertNotIn("VIEWER HTML:", out)
            self.assertNotIn("VIEWER ALIAS:", out)
            self.assertNotIn("Docker note:", out)
            self.assertNotIn("IMAGE DIR:", out)
            names = {p.name for p in Path(tmp).iterdir()}
            self.assertTrue(any(n.endswith("-current.png") for n in names))
            self.assertTrue(any(n.endswith("-previous.png") for n in names))
            self.assertIn("viewer.html", names)
            self.assertTrue(any(n.endswith("-viewer.html") for n in names))
            html_text = (Path(tmp) / "viewer.html").read_text(encoding="utf-8")
            self.assertIn("View current", html_text)
            self.assertIn("View previous", html_text)
            self.assertIn("Completed Date", html_text)
            self.assertIn("Request Date", html_text)
            self.assertIn("2026-09-09 14:15:11.466", html_text)
            svc.get_details.assert_called_once()

    def test_show_label_root_link_uses_public_url(self):
        db = FakeDB([
            [{"labels_user": "TWI", "labels_password": "1024"}],
            [{"id": 1, "etiqueta": "P9", "mac": "00:11:22:33:44:55", "activo": 1}],
        ])
        png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
        service = SolumLabelService(trace_id="cli-test")
        both = service.resolve_show_images(DETAIL_WITH_CONTENT)

        with tempfile.TemporaryDirectory() as tmp:
            env = {
                "SHOW_LABEL_IMAGE_DIR": tmp,
                "SHOW_LABEL_HTTP_PORT": "18766",
                "SHOW_LABEL_PUBLIC_URL": "http://labels.example:8765",
            }
            with patch.dict(os.environ, env, clear=False):
                with patch("commands.show_label.SolumLabelService") as svc_cls:
                    svc = svc_cls.return_value
                    svc.get_details.return_value = {
                        "status": "SUCCESS",
                        "status_at": "ON",
                        "last_connection": "2026-09-09 14:15:11.466",
                        "raw_data": DETAIL_WITH_CONTENT,
                    }
                    svc.resolve_show_images.return_value = both

                    mock_resp = MagicMock()
                    mock_resp.raise_for_status = MagicMock()
                    mock_resp.headers = {"Content-Type": "image/png"}
                    mock_resp.iter_content = MagicMock(return_value=[png_bytes])

                    with patch("services.label_image_viewer.requests.get", return_value=mock_resp):
                        with patch("services.label_image_viewer.has_display", return_value=False):
                            with patch("services.label_image_viewer.running_in_docker", return_value=True):
                                with patch("services.label_image_viewer.open_with_viewer", return_value=False):
                                    buf = io.StringIO()
                                    with redirect_stdout(buf):
                                        show_label.execute(db, "cli-test", ['"bd_twistic_demo"', "P9"])
                                    out = buf.getvalue()

            self.assertIn("ROOT LINK:", out)
            self.assertIn("http://labels.example:8765/", out)
            self.assertNotIn("http://127.0.0.1:18766/", out)
            self.assertIn("\033]8;;http://labels.example:8765/", out)

    def test_mac_lookup_query(self):
        db = FakeDB([
            [{"labels_user": "TWI", "labels_password": "1024"}],
            [{"id": 1, "etiqueta": "Room", "mac": "001122334455", "activo": 1}],
        ])
        with patch("commands.show_label.SolumLabelService") as svc_cls:
            svc = svc_cls.return_value
            svc.get_details.return_value = {
                "status": "SUCCESS",
                "raw_data": {"currentImage": [], "previousImage": []},
            }
            svc.resolve_show_images.return_value = {
                "current": {
                    "role": "current",
                    "source_key": "currentImage",
                    "page": None,
                    "state": "UNKNOWN",
                    "status_at": "UNKNOWN",
                    "completed_date": "N/A",
                    "request_date": "N/A",
                    "last_connection": "N/A",
                    "content_url": None,
                    "image": None,
                },
                "previous": {
                    "role": "previous",
                    "source_key": "previousImage",
                    "page": None,
                    "state": "UNKNOWN",
                    "status_at": "UNKNOWN",
                    "completed_date": "N/A",
                    "request_date": "N/A",
                    "last_connection": "N/A",
                    "content_url": None,
                    "image": None,
                },
            }
            buf = io.StringIO()
            with redirect_stdout(buf):
                show_label.execute(db, "cli-test", ["madrid", "00:11:22:33:44:55"])
        label_query = [q for q, _ in db.queries if "dispositivos_etiquetas" in q][0]
        self.assertIn("REPLACE", label_query)
        self.assertIn("No visualizable", buf.getvalue())


class LabelImageViewerUnitTests(unittest.TestCase):
    def test_guess_extension_from_content_type(self):
        self.assertEqual(viewer._guess_extension("image/png", "https://x"), ".png")
        self.assertEqual(viewer._guess_extension("image/jpeg", "https://x"), ".jpg")

    def test_public_base_url_priority(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SHOW_LABEL_PUBLIC_URL", None)
            os.environ.pop("SHOW_LABEL_HOST", None)
            with patch.dict(os.environ, {"SHOW_LABEL_HTTP_PORT": "8765"}, clear=False):
                self.assertEqual(viewer.public_base_url(), "http://127.0.0.1:8765")
                self.assertEqual(viewer.root_viewer_url(), "http://127.0.0.1:8765/")

            with patch.dict(
                os.environ,
                {"SHOW_LABEL_HOST": "srv.twistic.local", "SHOW_LABEL_HTTP_PORT": "8765"},
                clear=False,
            ):
                os.environ.pop("SHOW_LABEL_PUBLIC_URL", None)
                self.assertEqual(viewer.public_base_url(), "http://srv.twistic.local:8765")
                self.assertEqual(viewer.root_viewer_url(8765), "http://srv.twistic.local:8765/")

            with patch.dict(
                os.environ,
                {
                    "SHOW_LABEL_PUBLIC_URL": "http://labels.example:8765/",
                    "SHOW_LABEL_HOST": "ignored-host",
                    "SHOW_LABEL_HTTP_PORT": "9999",
                },
                clear=False,
            ):
                self.assertEqual(viewer.public_base_url(9999), "http://labels.example:8765")
                self.assertEqual(viewer.root_viewer_url(9999), "http://labels.example:8765/")
                self.assertEqual(
                    viewer.viewer_page_url("aa:bb", port=9999),
                    "http://labels.example:8765/view/AA_BB",
                )

    def test_osc8_and_clickable_format(self):
        link = viewer.osc8_hyperlink("http://127.0.0.1:8765/", "Open")
        self.assertIn("\033]8;;http://127.0.0.1:8765/\033\\", link)
        self.assertIn("Open", link)
        line = viewer.format_clickable_url("http://127.0.0.1:8765/view/ABC", "Open HTML viewer")
        self.assertIn("http://127.0.0.1:8765/view/ABC", line)
        self.assertIn("Open HTML viewer", line)

    def test_download_writes_file(self):
        png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.headers = {"Content-Type": "image/png"}
        mock_resp.iter_content = MagicMock(return_value=[png_bytes])
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "label"
            with patch("services.label_image_viewer.requests.get", return_value=mock_resp):
                saved = viewer.download_image("https://example.invalid/a.png?sig=fake", dest)
            self.assertTrue(saved.exists())
            self.assertEqual(saved.suffix, ".png")
            self.assertEqual(saved.read_bytes(), png_bytes)

    def test_write_viewer_html_and_http_routes(self):
        png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
        with tempfile.TemporaryDirectory() as tmp:
            cur = Path(tmp) / "MAC1-current.png"
            prev = Path(tmp) / "MAC1-previous.png"
            cur.write_bytes(png_bytes)
            prev.write_bytes(png_bytes)
            with patch.dict(os.environ, {"SHOW_LABEL_IMAGE_DIR": tmp, "SHOW_LABEL_HTTP_PORT": "18767"}, clear=False):
                specific, alias = viewer.write_viewer_html(
                    label_key="MAC1",
                    label_name="P9",
                    current={
                        "page": 2,
                        "state": "SUCCESS",
                        "completed_date": "2026-09-09 14:15:11.466",
                        "request_date": "2026-09-09 14:14:40.000",
                    },
                    previous={
                        "page": 2,
                        "state": "SUCCESS",
                        "completed_date": "2026-09-08 10:01:00.000",
                        "request_date": "2026-09-08 10:00:00.000",
                    },
                    current_file=cur,
                    previous_file=prev,
                )
                self.assertTrue(specific.exists())
                self.assertTrue(alias.exists())
                text = specific.read_text(encoding="utf-8")
                self.assertIn("View current", text)
                self.assertIn("View previous", text)
                self.assertIn("Completed Date", text)
                port = viewer.serve_http_root(Path(tmp), port=18767)
                self.assertEqual(port, 18767)
                # Fetch HTML via handler root and /view/
                import urllib.request
                with urllib.request.urlopen("http://127.0.0.1:18767/", timeout=2) as resp:
                    body = resp.read().decode("utf-8")
                self.assertIn("View current", body)
                with urllib.request.urlopen("http://127.0.0.1:18767/view/MAC1", timeout=2) as resp:
                    body2 = resp.read().decode("utf-8")
                self.assertIn("MAC1", body2)
                with urllib.request.urlopen("http://127.0.0.1:18767/MAC1-current.png", timeout=2) as resp:
                    self.assertEqual(resp.read()[:8], b"\x89PNG\r\n\x1a\n")

    def test_present_label_images_downloads_both(self):
        png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.headers = {"Content-Type": "image/png"}
        mock_resp.iter_content = MagicMock(return_value=[png_bytes])
        current = {
            "content_url": "https://example.invalid/c.png?sig=fake",
            "page": 1,
            "state": "SUCCESS",
            "completed_date": "2026-01-01 00:00:01",
            "request_date": "2026-01-01 00:00:00",
        }
        previous = {
            "content_url": "https://example.invalid/p.png?sig=fake",
            "page": 1,
            "state": "SUCCESS",
            "completed_date": "2025-12-31 00:00:01",
            "request_date": "2025-12-31 00:00:00",
        }
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"SHOW_LABEL_IMAGE_DIR": tmp, "SHOW_LABEL_HTTP_PORT": "18768"}, clear=False):
                with patch("services.label_image_viewer.requests.get", return_value=mock_resp):
                    with patch("services.label_image_viewer.has_display", return_value=False):
                        with patch("services.label_image_viewer.running_in_docker", return_value=True):
                            result = viewer.present_label_images("aa:bb", "Lab", current, previous)
            self.assertIsNotNone(result["current_file"])
            self.assertIsNotNone(result["previous_file"])
            self.assertTrue(str(result["current_file"]).endswith("-current.png"))
            self.assertTrue(str(result["previous_file"]).endswith("-previous.png"))
            self.assertIsNotNone(result["viewer_url"])
            self.assertIn("/view/", result["viewer_url"])
            self.assertTrue(result["root_url"].endswith("/"))
            self.assertTrue((Path(tmp) / "viewer.html").exists())

    def test_present_label_images_uses_show_label_host(self):
        png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.headers = {"Content-Type": "image/png"}
        mock_resp.iter_content = MagicMock(return_value=[png_bytes])
        current = {
            "content_url": "https://example.invalid/c.png?sig=fake",
            "page": 1,
            "state": "SUCCESS",
            "completed_date": "2026-01-01 00:00:01",
            "request_date": "2026-01-01 00:00:00",
        }
        previous = {
            "content_url": None,
            "page": None,
            "state": "UNKNOWN",
            "completed_date": "N/A",
            "request_date": "N/A",
        }
        with tempfile.TemporaryDirectory() as tmp:
            env = {
                "SHOW_LABEL_IMAGE_DIR": tmp,
                "SHOW_LABEL_HTTP_PORT": "18769",
                "SHOW_LABEL_HOST": "10.0.0.42",
            }
            with patch.dict(os.environ, env, clear=False):
                os.environ.pop("SHOW_LABEL_PUBLIC_URL", None)
                with patch("services.label_image_viewer.requests.get", return_value=mock_resp):
                    with patch("services.label_image_viewer.has_display", return_value=False):
                        with patch("services.label_image_viewer.running_in_docker", return_value=True):
                            result = viewer.present_label_images("aa:bb", "Lab", current, previous)
            self.assertEqual(result["root_url"], "http://10.0.0.42:18769/")
            self.assertIn("http://10.0.0.42:18769/view/", result["viewer_url"])
            self.assertNotIn("127.0.0.1", result["root_url"])


if __name__ == "__main__":
    unittest.main()
