from datetime import datetime
from typing import Optional
from pydantic import BaseModel, EmailStr

# ── Register ──────────────────────────────────────────────────────────────────
class RegisterRequest(BaseModel):
    name: str
    email: EmailStr
    password: str
    terms_accepted: bool
    privacy_accepted: bool

# ── Login ─────────────────────────────────────────────────────────────────────
class LoginRequest(BaseModel):
    email: EmailStr
    password: str

# ── Responses ─────────────────────────────────────────────────────────────────
class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"

class UserResponse(BaseModel):
    id: int
    name: str
    email: str
    is_active: bool
    is_admin: bool
    terms_accepted_at: Optional[datetime]
    privacy_accepted_at: Optional[datetime]
    created_at: datetime

    model_config = {"from_attributes": True}

# ── Admin ─────────────────────────────────────────────────────────────────────
class AdminRegisterRequest(BaseModel):
    name: str
    email: EmailStr
    password: str
    terms_accepted: bool
    privacy_accepted: bool

# ── Profile update ────────────────────────────────────────────────────────────
class UpdateProfileRequest(BaseModel):
    name: Optional[str] = None
    email: Optional[EmailStr] = None

class UpdatePasswordRequest(BaseModel):
    current_password: str
    new_password: str
    confirm_password: str
