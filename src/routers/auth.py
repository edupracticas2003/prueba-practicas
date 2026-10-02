# routers/auth.py
import uuid
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from services.auth_service import AuthService

router = APIRouter(prefix="/api/v1/auth", tags=["Authentication"])

class LoginRequest(BaseModel):
    username: str = Field(..., description="The unique identifier or email of the user.", examples=["admin_user"])
    password: str = Field(..., description="The cleartext password associated with the account.", examples=["P@ssw0rd123"])

@router.post(
    "/login",
    summary="Authenticate user credentials and issue a token",
    responses={
        200: {
            "description": "Successful authentication. Returns the access token along with traceability details.",
            "content": {
                "application/json": {
                    "example": {
                        "success": True,
                        "trace_id": "web-login-abc123xyz789",
                        "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                        "token_type": "Bearer",
                        "expired_at": "2026-09-16 11:21:00"
                    }
                }
            }
        },
        401: {"description": "Unauthorized. Invalid username, incorrect password, or account suspended."},
        500: {"description": "Internal Server Error during the authentication process."}
    }
)
def api_login(credentials: LoginRequest):
    """
    Verifies user identity using provided credentials against the authentication management system.

    ### Endpoint Flow:
    1. **Traceability:** Generates a secure `trace_id` dedicated to tracking this operational sequence.
    2. **Credential Validation:** Cross-references the `username` and `password` via `AuthService`.
    3. **Token Generation:** Upon a valid match, creates a JSON Web Token (JWT) with a standard 24-hour expiration window.
    """
    trace_id = f"web-login-{uuid.uuid4().hex[:12]}"
    auth = AuthService(trace_id=trace_id)
    result = auth.login_y_generar_token(credentials.username, credentials.password)
    
    if result["status"] == "ERROR":
        raise HTTPException(status_code=401, detail=result["message"])
        
    return {
        "success": True,
        "trace_id": trace_id,
        "token": result["token"],
        "token_type": "Bearer",
        "expired_at": result["expired_at"]
    }

