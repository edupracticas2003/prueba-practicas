# services/label_image_viewer.py
"""Descarga y visualización local de imágenes ESL (current/previous) + página HTML clickable."""
from __future__ import annotations

import html
import os
import shutil
import subprocess
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, unquote, urlparse

import requests

DEFAULT_IMAGE_DIR = "/tmp/com-labels-images"
DEFAULT_HTTP_PORT = 8765
_http_server_lock = threading.Lock()
_http_server: Optional[ThreadingHTTPServer] = None
_http_server_root: Optional[Path] = None
_http_server_port: Optional[int] = None


def image_dir() -> Path:
    root = os.getenv("SHOW_LABEL_IMAGE_DIR", DEFAULT_IMAGE_DIR).strip() or DEFAULT_IMAGE_DIR
    path = Path(root)
    path.mkdir(parents=True, exist_ok=True)
    return path


def http_port() -> int:
    raw = os.getenv("SHOW_LABEL_HTTP_PORT", str(DEFAULT_HTTP_PORT)).strip()
    try:
        port = int(raw)
    except ValueError:
        return DEFAULT_HTTP_PORT
    return port if 1 <= port <= 65535 else DEFAULT_HTTP_PORT


def public_base_url(port: Optional[int] = None) -> str:
    """
    Base URL printed for ROOT LINK / viewer links (reachable from the user's browser).

    Priority:
      1. SHOW_LABEL_PUBLIC_URL (full base, e.g. http://myserver:8765)
      2. SHOW_LABEL_HOST (+ SHOW_LABEL_HTTP_PORT) → http://<host>:<port>
      3. Fallback http://127.0.0.1:<port> (local-only; useless if Docker is remote)
    """
    listen_port = port if port is not None else http_port()
    public = (os.getenv("SHOW_LABEL_PUBLIC_URL") or "").strip()
    if public:
        return public.rstrip("/")
    host = (os.getenv("SHOW_LABEL_HOST") or "").strip()
    if host:
        if "://" in host:
            return host.rstrip("/")
        return f"http://{host}:{listen_port}"
    return f"http://127.0.0.1:{listen_port}"


def has_display() -> bool:
    return bool(os.getenv("DISPLAY") or os.getenv("WAYLAND_DISPLAY"))


def running_in_docker() -> bool:
    if os.path.exists("/.dockerenv"):
        return True
    try:
        cgroup = Path("/proc/1/cgroup").read_text(errors="ignore")
        return "docker" in cgroup or "containerd" in cgroup
    except OSError:
        return False


def safe_label_key(label_key: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in str(label_key).upper())[:48] or "label"


def _guess_extension(content_type: Optional[str], url: str) -> str:
    ctype = (content_type or "").split(";")[0].strip().lower()
    mapping = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "image/bmp": ".bmp",
    }
    if ctype in mapping:
        return mapping[ctype]
    path = urlparse(url).path.lower()
    for ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"):
        if path.endswith(ext):
            return ".jpg" if ext == ".jpeg" else ext
    return ".png"


def download_image(url: str, dest_path: Path, timeout: int = 20) -> Path:
    """Descarga el PNG/JPEG desde la URL SAS (sin Bearer Solum)."""
    response = requests.get(url, timeout=timeout, stream=True)
    response.raise_for_status()
    ext = _guess_extension(response.headers.get("Content-Type"), url)
    if dest_path.suffix.lower() not in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"):
        dest_path = dest_path.with_suffix(ext)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    with open(dest_path, "wb") as handle:
        for chunk in response.iter_content(chunk_size=65536):
            if chunk:
                handle.write(chunk)
    return dest_path


def open_with_viewer(path_or_url: str) -> bool:
    """Intenta abrir path/URL con el visor del sistema. True si el proceso arrancó."""
    target = str(path_or_url)
    candidates = []
    if shutil.which("xdg-open"):
        candidates.append(["xdg-open", target])
    if shutil.which("open"):
        candidates.append(["open", target])
    for cmd in candidates:
        try:
            subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return True
        except OSError:
            continue
    return False


def osc8_hyperlink(url: str, text: Optional[str] = None) -> str:
    """Enlace OSC-8 clickable en terminales compatibles + texto visible con URL."""
    label = text if text is not None else url
    return f"\033]8;;{url}\033\\{label}\033]8;;\033\\"


def format_clickable_url(url: str, text: Optional[str] = None) -> str:
    """Línea amigable: OSC-8 + URL plano (fallback si el terminal no soporta OSC-8)."""
    label = text if text is not None else "Open viewer"
    return f"{osc8_hyperlink(url, label)}  ({url})"


class _ShowLabelHandler(SimpleHTTPRequestHandler):
    """Sirve PNGs + HTML viewer (/ y /view/<mac>)."""

    def __init__(self, *args, directory: Optional[str] = None, **kwargs):
        super().__init__(*args, directory=directory, **kwargs)

    def log_message(self, format, *args):  # noqa: A003
        return

    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        path = unquote(parsed.path or "/")
        if path in ("/", "/index.html", "/viewer.html"):
            self._serve_latest_or_index()
            return
        if path.startswith("/view/"):
            mac = path[len("/view/") :].strip("/")
            if not mac or "/" in mac or ".." in mac:
                self.send_error(404, "Not found")
                return
            html_name = f"{safe_label_key(mac)}-viewer.html"
            target = Path(self.directory) / html_name
            if not target.is_file():
                # Also accept already-safe keys in the URL.
                target = Path(self.directory) / f"{mac}-viewer.html"
            if target.is_file():
                self.path = "/" + quote(target.name)
                return SimpleHTTPRequestHandler.do_GET(self)
            self.send_error(404, "Viewer not found for this label")
            return
        return SimpleHTTPRequestHandler.do_GET(self)

    def _serve_latest_or_index(self):
        root = Path(self.directory)
        latest = root / "viewer.html"
        if latest.is_file():
            self.path = "/viewer.html"
            return SimpleHTTPRequestHandler.do_GET(self)
        viewers = sorted(root.glob("*-viewer.html"), key=lambda p: p.stat().st_mtime, reverse=True)
        if viewers:
            self.path = "/" + quote(viewers[0].name)
            return SimpleHTTPRequestHandler.do_GET(self)
        body = (
            b"<!doctype html><html><body><h1>com-labels show-label</h1>"
            b"<p>No label viewer generated yet. Run <code>show-label</code> first.</p>"
            b"</body></html>"
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _ensure_http_server(root: Path, port: int) -> ThreadingHTTPServer:
    global _http_server, _http_server_root, _http_server_port
    with _http_server_lock:
        if (
            _http_server is not None
            and _http_server_root == root
            and _http_server_port == port
        ):
            return _http_server

        if _http_server is not None:
            try:
                _http_server.shutdown()
            except Exception:
                pass
            try:
                _http_server.server_close()
            except Exception:
                pass
            _http_server = None
            _http_server_root = None
            _http_server_port = None

        directory = str(root)

        class _BoundHandler(_ShowLabelHandler):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=directory, **kwargs)

        server = ThreadingHTTPServer(("0.0.0.0", port), _BoundHandler)
        thread = threading.Thread(target=server.serve_forever, name="show-label-http", daemon=True)
        thread.start()
        time.sleep(0.05)
        _http_server = server
        _http_server_root = root
        _http_server_port = port
        return server


def serve_http_root(root: Path, port: Optional[int] = None) -> int:
    listen_port = port if port is not None else http_port()
    _ensure_http_server(root.resolve(), listen_port)
    return listen_port


def serve_image_http(path: Path, port: Optional[int] = None) -> str:
    """Sirve el directorio de imágenes y devuelve la URL pública del fichero."""
    listen_port = serve_http_root(path.parent, port=port)
    return f"{public_base_url(listen_port)}/{path.name}"


def viewer_page_url(label_key: str, port: Optional[int] = None) -> str:
    listen_port = port if port is not None else http_port()
    return f"{public_base_url(listen_port)}/view/{quote(safe_label_key(label_key))}"


def root_viewer_url(port: Optional[int] = None) -> str:
    listen_port = port if port is not None else http_port()
    return f"{public_base_url(listen_port)}/"


def _role_card(role: str, meta: Dict[str, Any], image_name: Optional[str]) -> str:
    title = "Current image" if role == "current" else "Previous image"
    page = html.escape(str(meta.get("page") if meta.get("page") is not None else "N/A"))
    state = html.escape(str(meta.get("state") or "UNKNOWN"))
    completed = html.escape(str(meta.get("completed_date") or "N/A"))
    request = html.escape(str(meta.get("request_date") or "N/A"))
    if image_name:
        href = html.escape(image_name)
        img_block = (
            f'<a class="btn" href="{href}" target="_blank" rel="noopener">Open {html.escape(role)} image</a>'
            f'<div class="preview"><img src="{href}" alt="{html.escape(role)} label image"/></div>'
        )
    else:
        img_block = '<p class="missing">No image available for this slot.</p>'
    return f"""
    <section class="card">
      <h2>{html.escape(title)}</h2>
      <dl>
        <dt>Page</dt><dd>{page}</dd>
        <dt>State</dt><dd>{state}</dd>
        <dt>Completed Date</dt><dd>{completed}</dd>
        <dt>Request Date</dt><dd>{request}</dd>
      </dl>
      {img_block}
    </section>
    """


def write_viewer_html(
    label_key: str,
    label_name: str,
    current: Dict[str, Any],
    previous: Dict[str, Any],
    current_file: Optional[Path],
    previous_file: Optional[Path],
) -> Tuple[Path, Path]:
    """
    Escribe <MAC>-viewer.html y viewer.html (alias última etiqueta) en SHOW_LABEL_IMAGE_DIR.
    """
    root = image_dir()
    key = safe_label_key(label_key)
    specific = root / f"{key}-viewer.html"
    alias = root / "viewer.html"
    cur_name = current_file.name if current_file else None
    prev_name = previous_file.name if previous_file else None
    body = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>show-label {html.escape(key)}</title>
  <style>
    :root {{
      --bg: #f4f6f8;
      --card: #ffffff;
      --ink: #1a2332;
      --muted: #5b6775;
      --accent: #0b6e4f;
      --accent-ink: #ffffff;
      --line: #d7dee7;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Segoe UI", system-ui, sans-serif;
      background: linear-gradient(160deg, #e8eef5 0%, var(--bg) 45%, #eef7f2 100%);
      color: var(--ink);
      padding: 1.5rem;
    }}
    h1 {{ margin: 0 0 .25rem; font-size: 1.5rem; }}
    .sub {{ color: var(--muted); margin-bottom: 1.25rem; }}
    .grid {{ display: grid; gap: 1rem; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); }}
    .card {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 1rem 1.1rem 1.2rem;
      box-shadow: 0 8px 24px rgba(26, 35, 50, 0.06);
    }}
    .card h2 {{ margin: 0 0 .75rem; font-size: 1.1rem; }}
    dl {{ display: grid; grid-template-columns: auto 1fr; gap: .35rem .75rem; margin: 0 0 1rem; }}
    dt {{ color: var(--muted); }}
    dd {{ margin: 0; font-weight: 600; }}
    .btn {{
      display: inline-block;
      background: var(--accent);
      color: var(--accent-ink);
      text-decoration: none;
      padding: .65rem 1rem;
      border-radius: 8px;
      font-weight: 600;
    }}
    .btn:hover {{ filter: brightness(1.05); }}
    .preview {{ margin-top: 1rem; }}
    .preview img {{
      max-width: 100%;
      height: auto;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
    }}
    .missing {{ color: var(--muted); }}
    .actions {{ margin: 1rem 0 1.25rem; display: flex; flex-wrap: wrap; gap: .6rem; }}
  </style>
</head>
<body>
  <h1>Label {html.escape(str(label_name or "N/A"))}</h1>
  <p class="sub">MAC {html.escape(str(label_key))} · com-labels show-label viewer</p>
  <div class="actions">
    {"<a class='btn' href='" + html.escape(cur_name) + "' target='_blank' rel='noopener'>View current</a>" if cur_name else ""}
    {"<a class='btn' href='" + html.escape(prev_name) + "' target='_blank' rel='noopener'>View previous</a>" if prev_name else ""}
  </div>
  <div class="grid">
    {_role_card("current", current or {}, cur_name)}
    {_role_card("previous", previous or {}, prev_name)}
  </div>
</body>
</html>
"""
    specific.write_text(body, encoding="utf-8")
    alias.write_text(body, encoding="utf-8")
    return specific, alias


def download_role_image(url: str, label_key: str, role: str) -> Path:
    safe = safe_label_key(label_key)
    role_safe = "".join(ch if ch.isalnum() else "_" for ch in str(role).lower())[:24] or "current"
    dest = image_dir() / f"{safe}-{role_safe}.png"
    return download_image(url, dest)


def visualize_image(url: str, label_key: str, role: str = "current") -> Tuple[Path, Optional[str], bool]:
    """Compat: descarga una imagen, sirve HTTP y opcionalmente abre visor."""
    saved = download_role_image(url, label_key, role)
    opened = False
    if has_display():
        opened = open_with_viewer(str(saved.resolve()))
    http_url = None
    try:
        http_url = serve_image_http(saved)
    except OSError:
        http_url = None
    return saved, http_url, opened


def present_label_images(
    label_key: str,
    label_name: str,
    current: Dict[str, Any],
    previous: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Descarga current/previous, escribe HTML viewer, arranca HTTP y opcionalmente abre visor.
    """
    current_file = None
    previous_file = None
    errors: List[str] = []

    if current.get("content_url"):
        try:
            current_file = download_role_image(current["content_url"], label_key, "current")
        except Exception as err:
            errors.append(f"current: {err}")
    if previous.get("content_url"):
        try:
            previous_file = download_role_image(previous["content_url"], label_key, "previous")
        except Exception as err:
            errors.append(f"previous: {err}")

    html_path = None
    alias_path = None
    if current_file or previous_file:
        html_path, alias_path = write_viewer_html(
            label_key=label_key,
            label_name=label_name,
            current=current,
            previous=previous,
            current_file=current_file,
            previous_file=previous_file,
        )

    port = None
    viewer_url = None
    root_url = None
    try:
        if html_path:
            port = serve_http_root(image_dir())
            viewer_url = viewer_page_url(label_key, port=port)
            root_url = root_viewer_url(port=port)
    except OSError as err:
        errors.append(f"http: {err}")

    opened_local = False
    if has_display() and not running_in_docker():
        # Prefer opening the HTML viewer; also open PNGs as fallback presence.
        if viewer_url:
            opened_local = open_with_viewer(viewer_url)
        if not opened_local and html_path:
            opened_local = open_with_viewer(str(html_path.resolve()))
        if current_file:
            open_with_viewer(str(current_file.resolve()))
        if previous_file:
            open_with_viewer(str(previous_file.resolve()))
    elif has_display() and current_file:
        # Inside Docker with DISPLAY (X11 forward): open files, not host Chrome.
        opened_local = open_with_viewer(str(current_file.resolve()))
        if previous_file:
            open_with_viewer(str(previous_file.resolve()))

    return {
        "current_file": current_file,
        "previous_file": previous_file,
        "html_path": html_path,
        "alias_html_path": alias_path,
        "viewer_url": viewer_url,
        "root_url": root_url,
        "port": port or http_port(),
        "opened_local": opened_local,
        "in_docker": running_in_docker(),
        "has_display": has_display(),
        "errors": errors,
        "image_dir": image_dir(),
    }
