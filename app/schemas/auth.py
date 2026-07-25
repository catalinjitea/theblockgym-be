from datetime import datetime
from typing import Optional
from pydantic import BaseModel, EmailStr, computed_field

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
    freeze_start: Optional[datetime] = None
    freeze_end: Optional[datetime] = None
    # Days still freezable, not the plan's original cap. Zero once either the
    # day allowance or the number of permitted freezes is spent.
    max_freeze_days: Optional[int] = None
    # The plan's original entitlement and what has been spent, for "x of y"
    # display. Note freeze_days_used already includes an in-progress freeze.
    freeze_days_allowance: Optional[int] = None
    freeze_days_used: int = 0
    freezes_allowance: Optional[int] = None
    freezes_used: int = 0

    @computed_field
    @property
    def freezes_remaining(self) -> Optional[int]:
        if self.freezes_allowance is None:
            return None
        return max(0, self.freezes_allowance - self.freezes_used)

    @computed_field
    @property
    def is_frozen(self) -> bool:
        if self.freeze_start is None or self.freeze_end is None:
            return False
        return self.freeze_start <= datetime.utcnow() <= self.freeze_end

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
    is_trainer: bool = False
    terms_accepted_at: Optional[datetime]
    privacy_accepted_at: Optional[datetime]
    created_at: datetime
    latest_membership: Optional[LatestMembershipInfo] = None

    @classmethod
    def from_orm_with_membership(cls, user):
        from app.core.membership import available_freeze_days

        obj = cls.model_validate(user)
        if user.memberships:
            latest = user.memberships[0]
            obj.latest_membership = LatestMembershipInfo.model_validate(latest)
            obj.latest_membership.max_freeze_days = available_freeze_days(latest)
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
