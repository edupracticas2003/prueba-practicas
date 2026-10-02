# commands/list_antennas.py
from database import MySQLConnection
from tw_logger import log_loki
from services.solum_antennas import SolumAntennaService

# Esta variable la usará el main de forma automática para la ayuda
description = "Shows all registered antennas from the database"

pref_db = 'bd_feeltourist_'
def execute(db: MySQLConnection, trace_id: str, args: list):
    """Lógica para listar las antenas de forma ultra rápida reutilizando la conexión y con filtro de estado."""

    if not args:
        print("\n❌ Error: Missing database name.")
        print("💡 Usage: list-antennas <database_name|*> [active|inactive|all] [yes|no]")
        print("\n⚠️  CRITICAL ARGUMENT ORDER RULES:")
        print("   The position of the arguments is strict. You must supply them in the exact order shown.")
        print("   You cannot skip an intermediate argument. For example, to set the 3rd argument [yes|no],")
        print("   you MUST explicitly provide the 2nd argument (use 'all' or '*' if you don't want to filter).")
        print("\n📝 ARGUMENT BREAKDOWN:")
        print("   1. <database_name|*>  (Required)")
        print("      • Suffix of your target database (e.g., 'madrid', 'barcelona').")
        print("      • Use '*' to scan all databases (Warning: May cause high server load).")
        print("      • Wrap in quotes (e.g., 'mi_bd_completa') to bypass the prefix.")
        print("   2. [active|inactive|all] (Optional - Defaults to 'all')")
        print("      • Filters antennas by their local database status.")
        print("      • Accepts: 'active'/'1' or 'inactive'/'0'.")
        print("   3. [yes|no] (Optional - Defaults to 'no')")
        print("      • Controls whether to fetch and display live infrastructure status from the API.")
        print("      • 'yes' : Appends the 'STATUS AT' column (triggers API connection overhead).")
        print("      • 'no'  : Omits the column for an ultra-fast, local database-only response.")
        print("\n💡 EXAMPLES:")
        print("   ➔ tw-cli > list-antennas madrid active yes  (Specific DB, only active, shows API status)")
        print("   ➔ tw-cli > list-antennas * active yes       (All DBs, only active, shows API status)")
        print("   ➔ tw-cli > list-antennas barcelona          (Specific DB, shows all, hides API status)\n")
        return

    # 1. Resolver argumentos posicionales
    input_arg = args[0]
    status_filter = None
    show_status_at = False

    if len(args) == 2:
        clean_arg = args[1].strip().lower()
        if clean_arg in ("yes", "1", "true"):
            show_status_at = True
        elif clean_arg in ("active", "activo", "1"):
            status_filter = 1
        elif clean_arg in ("inactive", "inactivo", "0"):
            status_filter = 0
    elif len(args) > 2:
        clean_filter = args[1].strip().lower()
        if clean_filter in ("active", "activo", "1"):
            status_filter = 1
        elif clean_filter in ("inactive", "inactivo", "0"):
            status_filter = 0
            
        clean_status_at = args[2].strip().lower()
        if clean_status_at in ("yes", "1", "true"):
            show_status_at = True

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
        # Extraemos el contenido eliminando las comillas externas y omitimos el prefijo de feeltourist
        db_exacta = input_arg[1:-1].strip()
        log_loki("INFO", f"Búsqueda exacta detectada. Escaneando BD sin prefijo: {db_exacta}", trace_id=trace_id)
        databases_to_scan = [db_exacta]
    else:
        # Comportamiento normal por defecto heredado
        databases_to_scan = [f"{pref_db}{input_arg}"]

    resultados_totales = []

    try:
        # 3. Iteramos las bases de datos resueltas
        for target_db in databases_to_scan:
            log_loki("INFO", f"Ejecutando list-antennas en la base de datos: {target_db}", trace_id=trace_id)
            
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

            if status_filter is not None:
                query = f"SELECT id, nombre, mac, activo FROM `{target_db}`.etiquetas_aps WHERE activo = %s"
                params = (status_filter,)
            else:
                query = f"SELECT id, nombre, mac, activo FROM `{target_db}`.etiquetas_aps"
                params = ()
            
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
            print("\n⚠️  No antennas found matching the criteria.")
            return

        resultados_totales.sort(key=lambda x: str(x['nombre']).lower())

        # 4. Renderizado adaptativo de la tabla (Cabeceras planas, sin emojis)
        if show_status_at:
            print(f"\n{'DATABASE':<25} | {'ID':<5} | {'NAME':<25} | {'MAC':<20} | {'STATUS TW':<9} | {'STATUS AT':<10} | {'CH':<4} | {'FIRMWARE':<11} | {'LAST CONNECTION':<19}")
            print("-" * 163)
            antenna_service = SolumAntennaService(trace_id=trace_id)
        else:
            print(f"\n{'DATABASE':<25} | {'ID':<5} | {'NAME':<25} | {'MAC':<20} | {'STATUS TW':<9}")
            print("-" * 97)
        
        for row in resultados_totales:
            db_visual   = row['db_name'][:22] + "..." if len(row['db_name']) > 25 else row['db_name']
            name_visual = row['nombre'][:22] + "..." if len(row['nombre']) > 25 else row['nombre']
            mac_visual  = row['mac'][:17] + "..." if len(row['mac']) > 20 else row['mac']
            
            # Formateamos los estados. Guardamos por separado el texto para el cálculo del padding
            status_tw_text = "ON" if row['activo'] == 1 else "OFF"
            status_visual  = f"🟢 {status_tw_text}" if row['activo'] == 1 else f"🔴 {status_tw_text}"
            
            if show_status_at:
                status_at_text = "UNKNOWN"
                status_at_visual = f"🟣 {status_at_text}"
                ch_visual        = "N/A"
                fw_visual        = "N/A"
                date_visual      = "N/A"
                
                if row['mac'] and row['solum_company'] and row['solum_store']:
                    try:
                        api_res = antenna_service.get_details(
                            antenna_mac=row['mac'],
                            company=row['solum_company'],
                            store=row['solum_store']
                        )
                        if api_res.get("status") == "NOT_FOUND":
                            status_at_text = "NOT REG"
                            status_at_visual = f"❌ {status_at_text}"
                        elif api_res.get("status") == "SUCCESS":
                            status_at_text = api_res.get("status_at", "UNKNOWN")
                            # 🎯 CORRECCIÓN: Si contiene CONN o contiene ON, pinta el círculo verde limpio
                            status_at_visual = "🟢 ON" if ("CONN" in str(status_at_text).upper() or "ON" in str(status_at_text).upper()) else f"🔴 {status_at_text}"
                            
                            raw_data = api_res.get("raw_data") or {}
                            if raw_data:
                                ch_visual   = str(raw_data.get("dataChannel", "N/A"))
                                fw_visual   = str(raw_data.get("version", "N/A"))[:11]
                                date_visual = str(raw_data.get("lastConnectionDate", "N/A"))[:19]
                        elif api_res.get("status") == "BUSINESS_ERROR":
                            status_at_text = "STORE_ERR"
                            status_at_visual = f"❌ {status_at_text}"
                    except Exception:
                        status_at_text = "ERROR"
                        status_at_visual = f"⚠️ {status_at_text}"
                elif row['mac'] and (not row['solum_company'] or not row['solum_store']):
                    status_at_text = "NO_CFG"
                    status_at_visual = f"❌ {status_at_text}"
                
                # 🎯 COMPENSACIÓN DINÁMICA DE EMOJIS:
                # Python cuenta el emoji como 1, la terminal ocupa 2. Restamos 1 espacio para mantener la alineación.
                pad_tw = 9 - 1
                pad_at = 10 - 1
                
                print(f"{db_visual:<25} | {row['id']:<5} | {name_visual:<25} | {mac_visual:<20} | {status_visual:<{pad_tw}} | {status_at_visual:<{pad_at}} | {ch_visual:<4} | {fw_visual:<11} | {date_visual:<19}")
            else:
                pad_tw = 9 - 1
                print(f"{db_visual:<25} | {row['id']:<5} | {name_visual:<25} | {mac_visual:<20} | {status_visual:<{pad_tw}}")
        
        # 5. Cierre y resumen total
        if show_status_at:
            print("-" * 163)
        else:
            print("-" * 97)
            
        filtro_texto = ""
        if len(args) > 1 and status_filter is not None:
            filtro_texto = f" [{str(args[1]).upper()}]"
            
        print(f"📊 Total{filtro_texto} antennas found: {len(resultados_totales)}\n")

    except Exception as e:
        print(f"\n❌ Error executing command: {e}")
        