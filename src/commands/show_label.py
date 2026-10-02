# commands/show_label.py
"""CLI: visualizar current/previous de una etiqueta ESL con página HTML clickable."""
from __future__ import annotations

import re

from database import MySQLConnection
from tw_logger import log_loki
from services.solum_labels import SolumLabelService
from services.label_image_viewer import (
    DEFAULT_HTTP_PORT,
    DEFAULT_IMAGE_DIR,
    format_clickable_url,
    present_label_images,
)

description = "Shows current and previous Solum ESL label images via HTML viewer / local open"

pref_db = "bd_feeltourist_"
_MAC_RE = re.compile(r"^[0-9A-Fa-f:.-]{8,24}$")


def _usage():
    print("\n❌ Error: Missing arguments.")
    print("💡 Usage: show-label <database_name> <mac|label_name>")
    print("\n📝 ARGUMENT BREAKDOWN:")
    print("   1. <database_name>  (Required)")
    print("      • Suffix of your target database (e.g., 'madrid').")
    print("      • Wrap in quotes (e.g., '\"bd_twistic_demo\"') to bypass the prefix.")
    print("   2. <mac|label_name>  (Required)")
    print("      • Label MAC (with or without colons) OR name filter (SQL etiqueta LIKE).")
    print("\n🖼️  VISUALIZATION:")
    print(f"   • Saves <MAC>-current.png / <MAC>-previous.png under {DEFAULT_IMAGE_DIR}")
    print("     (SHOW_LABEL_IMAGE_DIR) plus an HTML viewer with buttons.")
    print(f"   • HTTP listener port {DEFAULT_HTTP_PORT} (SHOW_LABEL_HTTP_PORT; map -p 8765:8765).")
    print("   • ROOT LINK host: SHOW_LABEL_PUBLIC_URL (e.g. http://<servidor>:8765)")
    print("     or SHOW_LABEL_HOST (+ port). Do not use 127.0.0.1 when Docker is remote.")
    print("   • CLI prints an OSC-8 clickable ROOT LINK (+ plain URL fallback).")
    print("   • With a host DISPLAY (not typical Docker): tries xdg-open / open.")
    print("   • From Docker Linux you cannot launch Mac Chrome inside the container —")
    print("     click the printed link / open the HTML buttons on the host.")
    print("\n💡 EXAMPLES:")
    print("   ➔ tw-cli > show-label madrid P9")
    print("   ➔ tw-cli > show-label \"bd_twistic_demo\" 00:11:22:33:44:55")
    print("   ➔ tw-cli > show-label madrid 001122334455\n")


def _strip_quotes(raw: str) -> str:
    text = str(raw or "").strip()
    if (text.startswith("'") and text.endswith("'")) or (text.startswith('"') and text.endswith('"')):
        text = text[1:-1].strip()
    return text


def _resolve_target_db(input_arg: str) -> str:
    if (input_arg.startswith("'") and input_arg.endswith("'")) or (
        input_arg.startswith('"') and input_arg.endswith('"')
    ):
        return input_arg[1:-1].strip()
    return f"{pref_db}{input_arg}"


def _looks_like_mac(token: str) -> bool:
    flat = token.replace(":", "").replace("-", "").replace(".", "")
    return bool(_MAC_RE.match(token)) and len(flat) >= 8 and all(c in "0123456789abcdefABCDEF" for c in flat)


def _flat_mac(mac: str) -> str:
    return mac.replace(":", "").replace("-", "").replace(".", "").strip().upper()


def _load_solum_cfg(db: MySQLConnection, target_db: str, trace_id: str):
    company = ""
    store = ""
    try:
        query_cfg = (
            "SELECT labels_user, labels_password FROM bd_twistic_sat.clients_centers "
            "WHERE db_name = %s LIMIT 1"
        )
        res_cfg = db.ejecutar_consulta(query_cfg, (target_db,))
        if res_cfg:
            row_cfg = res_cfg[0] if isinstance(res_cfg, list) else res_cfg
            company = str(row_cfg.get("labels_user", "")).strip()
            store = str(row_cfg.get("labels_password", "")).strip()
    except Exception as cfg_err:
        log_loki("WARNING", f"Error leyendo configuración Solum en {target_db}: {cfg_err}", trace_id=trace_id)
    return company, store


def _find_labels(db: MySQLConnection, target_db: str, label_arg: str):
    label_arg = _strip_quotes(label_arg)
    if _looks_like_mac(label_arg):
        flat = _flat_mac(label_arg)
        query = (
            f"SELECT id, etiqueta, mac, activo FROM `{target_db}`.dispositivos_etiquetas "
            f"WHERE REPLACE(REPLACE(UPPER(mac), ':', ''), '-', '') = %s "
            f"OR UPPER(mac) = %s"
        )
        return db.ejecutar_consulta(query, (flat, label_arg.upper())) or []

    query = (
        f"SELECT id, etiqueta, mac, activo FROM `{target_db}`.dispositivos_etiquetas "
        f"WHERE etiqueta LIKE %s"
    )
    return db.ejecutar_consulta(query, (f"%{label_arg}%",)) or []


def _print_image_meta(title: str, display: dict, saved_path) -> None:
    page = display.get("page")
    state = display.get("state") or "UNKNOWN"
    completed = display.get("completed_date") or "N/A"
    request = display.get("request_date") or "N/A"
    print(f"\n--- {title} ---")
    print(f"📄 PAGE:             {page if page is not None else 'N/A'}")
    print(f"📶 STATE:            {state}")
    # Completed Date = statusUpdateTime; Request Date = processUpdateTime (labels/detail image object).
    print(f"✅ COMPLETED DATE:   {completed}")
    print(f"📨 REQUEST DATE:     {request}")
    if saved_path:
        print(f"💾 SAVED PATH:       {saved_path}")
    elif not display.get("content_url"):
        print(f"⚠️  No {display.get('source_key') or title}.content URL — image not available.")


def execute(db: MySQLConnection, trace_id: str, args: list):
    if len(args) < 2:
        _usage()
        return

    input_arg = args[0]
    label_arg = args[1]
    target_db = _resolve_target_db(input_arg)
    log_loki("INFO", f"Ejecutando show-label en {target_db} filtro={label_arg}", trace_id=trace_id)

    try:
        company, store = _load_solum_cfg(db, target_db, trace_id)
        if not company or not store:
            print(f"\n❌ Missing Solum company/store for database '{target_db}'.")
            print("   Check bd_twistic_sat.clients_centers (labels_user / labels_password).\n")
            return

        rows = _find_labels(db, target_db, label_arg)
        if not rows:
            print(f"\n⚠️  No labels found in '{target_db}' matching '{_strip_quotes(label_arg)}'.\n")
            return
        if len(rows) > 1:
            print(f"\n⚠️  Multiple labels matched '{_strip_quotes(label_arg)}'. Use the exact MAC:")
            for row in rows:
                print(f"   • {row.get('etiqueta') or 'N/A'}  MAC={row.get('mac') or 'N/A'}  id={row.get('id')}")
            print()
            return

        row = rows[0]
        label_name = str(row.get("etiqueta") or "N/A")
        label_mac = str(row.get("mac") or "").strip()
        if not label_mac:
            print(f"\n❌ Label '{label_name}' has no MAC in the database.\n")
            return

        service = SolumLabelService(trace_id=trace_id)
        detail = service.get_details(label_mac=label_mac, company=company, store=store)
        if detail.get("status") != "SUCCESS":
            print(f"\n❌ Solum labels/detail failed: {detail.get('status')}")
            print(f"   Label: {label_name}  MAC: {label_mac}\n")
            return

        images = service.resolve_show_images(detail.get("raw_data") or {})
        current = images.get("current") or {}
        previous = images.get("previous") or {}

        print(f"\n🏷️  LABEL:            {label_name}")
        print(f"🔢 MAC:              {label_mac}")

        result = present_label_images(
            label_key=label_mac,
            label_name=label_name,
            current=current,
            previous=previous,
        )
        for err in result.get("errors") or []:
            log_loki("ERROR", f"show-label present error: {err}", trace_id=trace_id)
            print(f"⚠️  {err}")

        _print_image_meta("CURRENT IMAGE", current, result.get("current_file"))
        _print_image_meta("PREVIOUS IMAGE", previous, result.get("previous_file"))

        if not result.get("current_file") and not result.get("previous_file"):
            print("\n⚠️  No visualizable currentImage/previousImage.content URLs in Solum detail.\n")
            return

        root_url = result.get("root_url")
        if root_url:
            print(f"\n🔗 ROOT LINK:        {format_clickable_url(root_url, 'Open latest viewer (/)')}")
        print()

    except Exception as e:
        log_loki("ERROR", f"show-label error: {e}", trace_id=trace_id)
        print(f"\n❌ Error executing command: {e}\n")
