"""Mock auth endpoints — token validation, simulated login."""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/auth", tags=["auth"])

VALID_TOKENS = {
    "sk-test-token-001": {"user_id": "user_001", "username": "alice", "role": "admin"},
    "sk-test-token-002": {"user_id": "user_002", "username": "bob", "role": "member"},
}


class LoginBody(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    user: dict[str, str]


@router.post("/login", response_model=TokenResponse, summary="Simulated login")
def login(body: LoginBody) -> TokenResponse:
    """Return a fake token for any username/password combo."""
    token = f"sk-test-{body.username}"
    return TokenResponse(
        access_token=token,
        user={"user_id": f"user_{body.username}", "username": body.username, "role": "member"},
    )


@router.get("/me", summary="Current user from token")
def get_me(authorization: str = Header(default="")) -> dict[str, str]:
    token = authorization.removeprefix("Bearer ").strip()
    info = VALID_TOKENS.get(token)
    if not info:
        raise HTTPException(status_code=401, detail="Invalid token")
    return info
