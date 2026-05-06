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

class LatestMembershipInfo(BaseModel):
    id: int
    plan: str
    start_date: datetime
    end_date: datetime
    status: str

    model_config = {"from_attributes": True}

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
    latest_membership: Optional[LatestMembershipInfo] = None

    @classmethod
    def from_orm_with_membership(cls, user):
        obj = cls.model_validate(user)
        if user.memberships:
            obj.latest_membership = LatestMembershipInfo.model_validate(user.memberships[0])
        return obj

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

# ── Password reset ────────────────────────────────────────────────────────────
class ForgotPasswordRequest(BaseModel):
    email: EmailStr
    lang: str = "ro"

class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str
    confirm_password: str
