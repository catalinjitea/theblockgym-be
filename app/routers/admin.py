from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import require_admin
from app.core.security import hash_password
from app.models.membership import Membership
from app.models.user import User
from app.schemas.auth import AdminRegisterRequest, UserResponse
from app.schemas.membership import MembershipResponse

router = APIRouter()

# ── Plan durations and amounts ────────────────────────────────────────────────
PLAN_CONFIG = {
    "lunar": {"duration_days": 30,  "amount": 22000},
    "3luni": {"duration_days": 90,  "amount": 58500},
    "6luni": {"duration_days": 180, "amount": 108000},
    "anual": {"duration_days": 365, "amount": 204000},
}


# ── GET /admin/users ──────────────────────────────────────────────────────────
@router.get("/users", dependencies=[Depends(require_admin)], response_model=list[UserResponse])
async def list_users(
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).order_by(User.created_at.desc()))
    return result.scalars().all()


# ── POST /admin/users ─────────────────────────────────────────────────────────
@router.post("/users", dependencies=[Depends(require_admin)], response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def admin_register_user(
    body: AdminRegisterRequest,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    if not body.terms_accepted:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Termenii și condițiile trebuie acceptate.")
    if not body.privacy_accepted:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Politica de confidențialitate trebuie acceptată.")

    result = await db.execute(select(User).where(User.email == body.email))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered.")

    now = datetime.utcnow()
    user = User(
        name=body.name,
        email=body.email,
        hashed_password=hash_password(body.password),
        terms_accepted_at=now,
        privacy_accepted_at=now,
    )
    db.add(user)
    await db.flush()
    return user


# ── POST /admin/users/{id}/memberships ───────────────────────────────────────
class AssignMembershipRequest(BaseModel):
    plan: str
    start_date: str

@router.post("/users/{user_id}/memberships", dependencies=[Depends(require_admin)], response_model=MembershipResponse, status_code=status.HTTP_201_CREATED)
async def assign_membership(
    user_id: int,
    body: AssignMembershipRequest,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")

    plan = PLAN_CONFIG.get(body.plan)
    if not plan:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Plan '{body.plan}' invalid.")

    try:
        start = datetime.fromisoformat(body.start_date)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Format dată invalid. Folosește YYYY-MM-DD.")

    end = start + timedelta(days=plan["duration_days"])

    membership = Membership(
        user_id=user.id,
        plan=body.plan,
        status="activ",
        amount=plan["amount"],
        start_date=start,
        end_date=end,
    )
    db.add(membership)
    await db.flush()
    return membership


# ── PATCH /admin/users/{id}/deactivate ───────────────────────────────────────
@router.patch("/users/{user_id}/deactivate", dependencies=[Depends(require_admin)], response_model=UserResponse)
async def deactivate_user(
    user_id: int,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    if user_id == admin.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot deactivate your own account.")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")

    user.is_active = False
    return user


# ── PATCH /admin/users/{id}/activate ─────────────────────────────────────────
@router.patch("/users/{user_id}/activate", dependencies=[Depends(require_admin)], response_model=UserResponse)
async def activate_user(
    user_id: int,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")

    user.is_active = True
    return user
