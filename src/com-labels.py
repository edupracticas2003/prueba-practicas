# com-label.py
import os 
import sys
import uuid
import importlib
import getpass

from database import MySQLConnection
from services.trace_log import ORIGEN_COMANDO, TraceLogService
from tw_logger import log_loki

COMMANDS_DIR = "commands"
commands_registry = {}

def load_commands():
    """Escanea la carpeta 'commands/' e importa dinámicamente los módulos."""
    if not os.path.exists(COMMANDS_DIR):
        return

    for filename in os.listdir(COMMANDS_DIR):
        if filename.endswith(".py") and filename != "__init__.py":
            command_name = filename[:-3].replace("_", "-")  # Transforma 'list_antennas' en 'list-antennas'
            module_name = f"{COMMANDS_DIR}.{filename[:-3]}"
            
            try:
                module = importlib.import_module(module_name)
                # Verificamos que cumpla con la interfaz requerida
                if hasattr(module, "execute") and hasattr(module, "description"):
                    commands_registry[command_name] = module
            except Exception as e:
                print(f"⚠️ Error loading command from {filename}: {e}")

def mostrar_ayuda():
    """Genera la ayuda en pantalla dinámicamente basándose en los comandos reales."""
    print("\n📚 Available Commands:")
    for name, module in sorted(commands_registry.items()):
        print(f"  {name:<15} - {module.description}")
    print("  help            - Shows this help message")
    print("  exit            - Closes the application\n")


def iniciar_cli():
    session_trace_id = f"cli-{uuid.uuid4().hex[:12]}"
    log_loki("INFO", "CLI Session started", trace_id=session_trace_id)
    
    # Cargamos todos los ficheros .py de la carpeta commands/
    load_commands()
    
    print("=" * 50)
    print(" 📡 ANTENNA MANAGEMENT SYSTEM CLI ")
    print("=" * 50)

    # 🔐 1. AUTENTICACIÓN PREVIA OBLIGATORIA (Base de datos general _COM)
    print("\n🔐 Por favor, inicie sesión para acceder al sistema:")
    intentos = 0
    autenticado = False
    
    while intentos < 3:
        try:
            username_input = input("Usuario: ").strip()
            # Oculta la clave de forma nativa en Docker sin eco en pantalla
            password_input = getpass.getpass("Contraseña (oculta): ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nGoodbye!")
            return

        if not username_input or not password_input:
            print("❌ El usuario y la contraseña no pueden estar vacíos.\n")
            intentos += 1
            continue

        query_auth = """
            SELECT user_name FROM tw_com_labels.api_tokens 
            WHERE user_name = %s AND password = %s
        """
        
        try:
            # Conexión dedicada a las credenciales en la BD general _COM
            with MySQLConnection(suffix="_COM", trace_id=session_trace_id) as db_auth:
                res_auth = db_auth.ejecutar_consulta(query_auth, (username_input, password_input))
                
                if res_auth and len(res_auth) > 0:
                    autenticado = True
                    log_loki("INFO", f"CLI user {username_input} authenticated successfully", trace_id=session_trace_id)
                    print("✅ ¡Acceso concedido!\n")
                    break
                else:
                    print("❌ Credenciales incorrectas.")
        except Exception as auth_err:
            log_loki("ERROR", f"Error durante la autenticación de CLI: {auth_err}", trace_id=session_trace_id)
            print("⚠️ Error de conexión al validar credenciales.")
        
        intentos += 1
        print(f"Te quedan {3 - intentos} intentos.\n")

    if not autenticado:
        log_loki("WARNING", "CLI login failed: Max attempts reached", trace_id=session_trace_id)
        print("❌ Número máximo de intentos alcanzado. Aplicación cerrada.")
        return

    # 📡 2. BUCLE INTERACTIVO ORIGINAL MODIFICADO EN EL EXIT (_DATA)
    mostrar_ayuda()

    try:
        with MySQLConnection(suffix="_DATA", trace_id=session_trace_id) as db:
            while True:
                try:
                    # Capturamos la entrada y la dividimos por si pasan argumentos adicionales en el futuro
                    raw_input = input("tw-cli > ").strip()
                    if not raw_input:
                        continue
                        
                    parts = raw_input.split()
                    command_name = parts[0].lower()
                    args = parts[1:]
                    
                    if command_name == "exit":
                        # 🎯 NUEVA CONFIRMACIÓN EN INGLÉS ANTES DE CERRAR EL CONTENEDOR
                        print("\n⚠️ WARNING: This action will completely stop the Docker container.")
                        confirm = input("Do you really want to close the container? (yes/no): ").strip().lower()
                        
                        if confirm == "yes":
                            log_loki("INFO", "CLI Session and container closed by user confirmation", trace_id=session_trace_id)
                            print("Goodbye!")
                            sys.exit(0)  # Mata el hilo principal de Python, deteniendo el contenedor
                        else:
                            print("❌ Exit cancelled. Returning to prompt.\n")
                            continue
                        
                    elif command_name == "help":
                        mostrar_ayuda()
                        
                    elif command_name in commands_registry:
                        # Una acción de usuario → una fila trace_log (origen=comando).
                        tracer = TraceLogService(loki_trace_id=session_trace_id)
                        # payload = solo la petición entrante (sin trace_id).
                        # correlation_id = session_trace_id para que SolumCoreClient
                        # encuentre la fila aunque ContextVar se pierda.
                        tracer.begin(
                            ORIGEN_COMANDO,
                            {
                                "command": command_name,
                                "args": args,
                                "raw": raw_input,
                            },
                            correlation_id=session_trace_id,
                        )
                        try:
                            commands_registry[command_name].execute(db, session_trace_id, args)
                        finally:
                            tracer.clear()

                    else:
                        print(f"❌ Unknown command: '{command_name}'. Type 'help' to see available commands.")
                        
                except (KeyboardInterrupt, EOFError):
                    print("\nGoodbye!")
                    break
                    
    except Exception as e:
        log_loki("CRITICAL", f"Failed to initialize database connection for CLI: {e}", trace_id=session_trace_id)
        print(f"Critical Error: Could not connect to database. Check logs.")

if __name__ == "__main__":
    iniciar_cli()

