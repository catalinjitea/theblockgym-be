from datetime import datetime
from typing import Optional
from pydantic import BaseModel, EmailStr

# ── Register ──────────────────────────────────────────────────────────────────
class RegisterRequest(BaseModel):
    first_name: str
    last_name: str
    email: EmailStr
    password: str
    phone_number: str
    age: Optional[int] = None
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
    first_name: str
    last_name: str
    email: str
    phone_number: str
    age: Optional[int]
    is_active: bool
    is_admin: bool
    terms_accepted_at: Optional[datetime]
    privacy_accepted_at: Optional[datetime]
    created_at: datetime

    model_config = {"from_attributes": True}

# ── Admin ─────────────────────────────────────────────────────────────────────
class AdminRegisterRequest(BaseModel):
    first_name: str
    last_name: str
    email: EmailStr
    password: str
    phone_number: str
    age: Optional[int] = None
    terms_accepted: bool
    privacy_accepted: bool

# ── Profile update ────────────────────────────────────────────────────────────
class UpdateProfileRequest(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[EmailStr] = None
    phone_number: Optional[str] = None
    age: Optional[int] = None

class UpdatePasswordRequest(BaseModel):
    current_password: str
    new_password: str
    confirm_password: str
