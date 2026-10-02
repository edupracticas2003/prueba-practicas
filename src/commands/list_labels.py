# commands/list_labels.py
from database import MySQLConnection
from tw_logger import log_loki
from services.solum_core import is_solum_timeout
from services.solum_labels import SolumLabelService, combine_status_at

# Esta variable la usará el main de forma automática para la ayuda
description = "Shows all registered device labels from the database"

pref_db = 'bd_feeltourist_'
def execute(db: MySQLConnection, trace_id: str, args: list):
    """Lógica para listar las etiquetas de forma ultra rápida reutilizando la conexión y con filtro de estado."""

    if not args:
        print("\n❌ Error: Missing database name.")
        print("💡 Usage: list-labels <database_name|*> [active|inactive|all] [yes|no] [label_name]")
        print("\n⚠️  CRITICAL ARGUMENT ORDER RULES:")
        print("   The position of the arguments is strict. You must supply them in the exact order shown.")
        print("   You cannot skip an intermediate argument. For example, to set the 3rd argument [yes|no],")
        print("   you MUST explicitly provide the 2nd argument (use 'all' or '*' if you don't want to filter).")
        print("   [label_name] is optional and always LAST. Omit it to list every name.")
        print("\n📝 ARGUMENT BREAKDOWN:")
        print("   1. <database_name|*>  (Required)")
        print("      • Suffix of your target database (e.g., 'madrid', 'barcelona').")
        print("      • Use '*' to scan all databases (Warning: May cause high server load).")
        print("      • Wrap in quotes (e.g., 'mi_bd_completa') to bypass the prefix.")
        print("   2. [active|inactive|all] (Optional - Defaults to 'all')")
        print("      • Filters labels by their local database status.")
        print("      • Accepts: 'active'/'1' or 'inactive'/'0'.")
        print("   3. [yes|no] (Optional - Defaults to 'no')")
        print("      • Controls whether to fetch and display live infrastructure status from the API.")
        print("      • 'yes' : Appends the 'STATUS AT' column (triggers API connection overhead).")
        print("      • 'no'  : Omits the column for an ultra-fast, local database-only response.")
        print("   4. [label_name] (Optional - Defaults to no name filter)")
        print("      • Filters by label name (SQL column `etiqueta` in dispositivos_etiquetas).")
        print("      • Example: P9  →  WHERE etiqueta LIKE '%P9%'")
        print("      • Empty / omitted = no name filter. Current commands without this arg keep working.")
        print("\n💡 EXAMPLES:")
        print("   ➔ tw-cli > list-labels madrid active yes  (Specific DB, only active, shows API status)")
        print("   ➔ tw-cli > list-labels * active yes       (All DBs, only active, shows API status)")
        print("   ➔ tw-cli > list-labels barcelona          (Specific DB, shows all, hides API status)")
        print("   ➔ tw-cli > list-labels \"bd_twistic_demo\" yes P9  (Exact DB, API status, name contains P9)")
        print("   ➔ tw-cli > list-labels madrid active yes P9 (Active only, API status, name contains P9)")
        print("   ➔ tw-cli > list-labels madrid all no P9    (All local statuses, no API, name contains P9)\n")
        return

    # 1. Resolver argumentos posicionales
    input_arg = args[0]
    status_filter = None
    show_status_at = False
    label_name_filter = None

    def _strip_name(raw):
        text = str(raw or "").strip()
        if (text.startswith("'") and text.endswith("'")) or (text.startswith('"') and text.endswith('"')):
            text = text[1:-1].strip()
        return text or None

    def _is_solum_token(raw):
        return str(raw).strip().lower() in ("yes", "no", "1", "true", "0", "false")

    def _is_yes(raw):
        return str(raw).strip().lower() in ("yes", "1", "true")

    def _parse_status_token(raw):
        token = str(raw).strip().lower()
        if token in ("active", "activo", "1"):
            return 1
        if token in ("inactive", "inactivo", "0"):
            return 0
        return None

    if len(args) == 2:
        clean_arg = args[1].strip().lower()
        if _is_yes(args[1]):
            show_status_at = True
        elif clean_arg in ("active", "activo"):
            status_filter = 1
        elif clean_arg in ("inactive", "inactivo", "0"):
            status_filter = 0
        elif clean_arg not in ("all", "*", "no", "false"):
            label_name_filter = _strip_name(args[1])
    elif len(args) == 3:
        first = args[1].strip().lower()
        second = args[2].strip().lower()
        if _is_yes(args[1]) or first in ("no", "false"):
            show_status_at = _is_yes(args[1])
            if not _is_solum_token(args[2]):
                label_name_filter = _strip_name(args[2])
        else:
            status_filter = _parse_status_token(args[1])
            if _is_yes(args[2]):
                show_status_at = True
            elif not _is_solum_token(args[2]) and second not in ("all", "*"):
                label_name_filter = _strip_name(args[2])
    elif len(args) > 3:
        status_filter = _parse_status_token(args[1])
        show_status_at = _is_yes(args[2])
        label_name_filter = _strip_name(args[3])

    # 2. Determinar las bases de datos a escanear
    es_busqueda_exacta = (input_arg.startswith("'") and input_arg.endswith("'")) or (input_arg.startswith('"') and input_arg.endswith('"'))

    if input_arg == "*":
        log_loki("INFO", "Comodín '*' detectado. Escaneando bases de datos", trace_id=trace_id)
        query_dbs = "SELECT schema_name FROM information_schema.schemata WHERE schema_name LIKE %s"
        res_dbs = db.ejecutar_consulta(query_dbs, (f"{pref_db}%",))

        if not res_dbs:
            print(f"\n⚠️  No databases found with prefix '{pref_db}'.")
            return

        databases_to_scan = []
        for row in res_dbs:
            if isinstance(row, dict):
                databases_to_scan.append(row.get('schema_name') or list(row.values())[0])
            else:
                databases_to_scan.append(row[0])
    elif es_busqueda_exacta:
        db_exacta = input_arg[1:-1].strip()
        log_loki("INFO", f"Búsqueda exacta detectada. Escaneando BD sin prefijo: {db_exacta}", trace_id=trace_id)
        databases_to_scan = [db_exacta]
    else:
        databases_to_scan = [f"{pref_db}{input_arg}"]

    resultados_totales = []

    try:
        # 3. Iteramos las bases de datos resueltas
        for target_db in databases_to_scan:
            log_loki("INFO", f"Ejecutando list-labels en la base de datos: {target_db}", trace_id=trace_id)

            company_db = ""
            store_db = ""

            if show_status_at:
                try:
                    query_cfg = f"SELECT labels_user, labels_password FROM bd_twistic_sat.clients_centers WHERE db_name = %s LIMIT 1"
                    res_cfg = db.ejecutar_consulta(query_cfg, (target_db,))
                    if res_cfg:
                        row_cfg = res_cfg[0] if isinstance(res_cfg, list) else res_cfg
                        company_db = str(row_cfg.get('labels_user', '')).strip()
                        store_db = str(row_cfg.get('labels_password', '')).strip()
                except Exception as cfg_err:
                    log_loki("WARNING", f"Error leyendo configuración en {target_db}: {cfg_err}", trace_id=trace_id)

            query = f"SELECT id, etiqueta, mac, activo FROM `{target_db}`.dispositivos_etiquetas"
            where_parts = []
            params = []
            if status_filter is not None:
                where_parts.append("activo = %s")
                params.append(status_filter)
            if label_name_filter:
                where_parts.append("etiqueta LIKE %s")
                params.append(f"%{label_name_filter}%")
            if where_parts:
                query = f"{query} WHERE {' AND '.join(where_parts)}"
            params = tuple(params)

            try:
                resultados = db.ejecutar_consulta(query, params)
                if resultados:
                    db_limpia = target_db.replace(pref_db, "")
                    for row in resultados:
                        row['db_name'] = db_limpia
                        row['solum_company'] = company_db
                        row['solum_store'] = store_db
                        resultados_totales.append(row)
            except Exception as db_err:
                log_loki("WARNING", f"Error leyendo {target_db}: {db_err}", trace_id=trace_id)

        if not resultados_totales:
            print("\n⚠️  No labels found matching the criteria.")
            return

        resultados_totales.sort(key=lambda x: str(x['etiqueta'] or '').lower())

        # 4. Renderizado adaptativo de la tabla (Cabeceras planas, sin emojis)
        if show_status_at:
            print(f"\n{'DATABASE':<25} | {'ID':<5} | {'LABEL NAME':<25} | {'MAC':<20} | {'STATUS TW':<9} | {'STATUS AT':<12} | {'TOTAL PAGE':<11} | {'NFC':<5} | {'RESPONSE MESSAGE':<17} | {'LAST CONNECTION':<19}")
            print("-" * 180)
            label_service = SolumLabelService(trace_id=trace_id)
        else:
            print(f"\n{'DATABASE':<25} | {'ID':<5} | {'LABEL NAME':<25} | {'MAC':<20} | {'STATUS TW':<9}")
            print("-" * 97)

        for row in resultados_totales:
            str_db   = str(row.get('db_name') or '')
            str_name = str(row.get('etiqueta') or 'N/A')
            str_mac  = str(row.get('mac') or 'N/A')

            db_visual   = str_db[:22] + "..." if len(str_db) > 25 else str_db
            name_visual = str_name[:22] + "..." if len(str_name) > 25 else str_name
            mac_visual  = str_mac[:17] + "..." if len(str_mac) > 20 else str_mac

            status_tw_text = "ON" if row.get('activo') == 1 else "OFF"
            status_visual  = f"🟢 {status_tw_text}" if row.get('activo') == 1 else f"🔴 {status_tw_text}"

            if show_status_at:
                status_at_text = "UNKNOWN"
                status_at_visual = f"🟣 {status_at_text}"
                total_page_visual = "N/A"
                nfc_visual        = "N/A"
                resp_visual       = "N/A"
                date_visual       = "N/A"

                if row.get('mac') and row.get('solum_company') and row.get('solum_store'):
                    try:
                        api_res = label_service.get_details(
                            label_mac=row['mac'],
                            company=row['solum_company'],
                            store=row['solum_store']
                        )
                        type_res = {}
                        try:
                            type_res = label_service.get_type_info(
                                label_mac=row['mac'],
                                company=row['solum_company'],
                                store=row['solum_store']
                            ) or {}
                        except Exception as type_err:
                            log_loki("ERROR", f"type/info failed for {row.get('mac')}: {type_err}", trace_id=trace_id)
                            type_res = {"status": "TIMEOUT" if is_solum_timeout(type_err) else "API_ERROR"}
                        status_at_text = combine_status_at(api_res)
                        status_key = str(status_at_text).strip().upper()
                        if status_key == "ON":
                            status_at_visual = "🟢 ON"
                        elif status_key == "UNKNOWN":
                            status_at_visual = f"🟣 {status_at_text}"
                        elif status_key == "TIMEOUT":
                            status_at_visual = f"🟡 {status_at_text}"
                        elif status_key == "ERROR":
                            status_at_visual = f"⚠️ {status_at_text}"
                        else:
                            status_at_visual = f"🔴 {status_at_text}"
                        if api_res.get("status") == "SUCCESS":
                            date_visual = str(api_res.get("last_connection") or "N/A")[:19]
                        if type_res.get("status") == "SUCCESS":
                            total_page_visual = str(type_res.get("totalPage") if type_res.get("totalPage") not in (None, "") else "N/A")[:11]
                            nfc_visual = str(type_res.get("nfc") if type_res.get("nfc") not in (None, "") else "N/A")[:5]
                            resp_visual = str(type_res.get("responseMessage") if type_res.get("responseMessage") not in (None, "") else "N/A")[:17]
                        elif type_res.get("status") == "NOT_FOUND":
                            total_page_visual = nfc_visual = resp_visual = "NOT REG"
                        elif type_res.get("status"):
                            fail_txt = "ERROR" if type_res.get("status") != "EMPTY" else "EMPTY"
                            total_page_visual = nfc_visual = resp_visual = fail_txt
                            log_loki("WARNING", f"type/info {type_res.get('status')} mac={row.get('mac')}", trace_id=trace_id)
                    except Exception:
                        status_at_text = "ERROR"
                        status_at_visual = f"⚠️ {status_at_text}"
                elif row.get('mac') and (not row.get('solum_company') or not row.get('solum_store')):
                    status_at_text = "NO_CFG"
                    status_at_visual = f"❌ {status_at_text}"

                pad_tw = 9 - 1
                pad_at = 12 - 1

                print(f"{db_visual:<25} | {row['id']:<5} | {name_visual:<25} | {mac_visual:<20} | {status_visual:<{pad_tw}} | {status_at_visual:<{pad_at}} | {total_page_visual:<11} | {nfc_visual:<5} | {resp_visual:<17} | {date_visual:<19}")
            else:
                pad_tw = 9 - 1
                print(f"{db_visual:<25} | {row['id']:<5} | {name_visual:<25} | {mac_visual:<20} | {status_visual:<{pad_tw}}")

        # 5. Cierre y resumen total
        if show_status_at:
            print("-" * 180)
        else:
            print("-" * 97)

        filtro_texto = ""
        if len(args) > 1 and status_filter is not None:
            filtro_texto = f" [{str(args[1]).upper()}]"
        if label_name_filter:
            filtro_texto += f" [{label_name_filter}]"

        print(f"📊 Total{filtro_texto} labels found: {len(resultados_totales)}\n")

    except Exception as e:
        print(f"\n❌ Error executing command: {e}")
