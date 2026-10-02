# api_server.py
import json

from fastapi import FastAPI, Request
from routers import auth_router, antennas_router, labels_router
from services.trace_log import ORIGEN_APLICACION, TraceLogService

# 📝 English documentation using Markdown formatting for Swagger UI
description = """
Solum Infrastructure Bridge API helps you manage infrastructure connectivity, database monitoring, and authorization. 🚀

## Features
You can perform the following operations:

* **Authentication**: Secure login, bearer token generation, and session management.
* **Antennas**: Bulk or specific multi-tenant database status monitoring integrated with Solum API.
* **Labels**: Bulk or specific multi-tenant ESL/label status monitoring integrated with Solum API.

## Support
For technical support, contact the DevOps team or visit the internal repository.
"""

app = FastAPI(
    title="Solum Infrastructure Bridge API",
    description=description,
    version="1.2.1",
    contact={
        "name": "Solum Infrastructure Team",
        "email": "direccion.tecnica@twisticdigital.com",
    },
)


async def _read_json_body(request: Request):
    """Lee el body una vez; el middleware lo reinyecta en el Request downstream."""
    try:
        body_bytes = await request.body()
    except Exception:
        return None, b""

    if not body_bytes:
        return None, body_bytes
    try:
        return json.loads(body_bytes.decode("utf-8")), body_bytes
    except Exception:
        return {"_raw": body_bytes.decode("utf-8", errors="replace")[:2000]}, body_bytes


@app.middleware("http")
async def trace_log_middleware(request: Request, call_next):
    """
    Una petición org /api/v1/* → una fila trace_log (origen=aplicacion).
    Token-Header / Authorization no se persisten; body se sanitiza en TraceLogService.
    """
    path = request.url.path or ""
    if not path.startswith("/api/v1/"):
        return await call_next(request)

    body_obj, body_bytes = await _read_json_body(request)

    # Reinyecta el body para que FastAPI/Pydantic puedan leerlo otra vez.
    async def receive():
        return {"type": "http.request", "body": body_bytes, "more_body": False}

    request = Request(request.scope, receive)

    tracer = TraceLogService(loki_trace_id=f"api-{path}")
    row_id = tracer.begin(
        ORIGEN_APLICACION,
        {
            "method": request.method,
            "path": path,
            "query": dict(request.query_params) if request.query_params else {},
            "body": body_obj,
        },
    )
    # request.state sobrevive al threadpool sync; ContextVar del middleware async no.
    request.state.trace_log_row_id = row_id
    try:
        return await call_next(request)
    finally:
        TraceLogService.unregister_row(row_id)
        tracer.clear()


# 🔌 REGISTRO CENTRAL DE LOS MÓDULOS DE FORMA LIMPIA
app.include_router(auth_router)
app.include_router(antennas_router)
app.include_router(labels_router)
