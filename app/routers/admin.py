import uuid
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel
from sqlalchemy import func, select, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.core.dependencies import require_admin
from app.core.security import hash_password
from app.models.membership import Membership
from app.models.membership_plan import MembershipPlan
from app.models.qr_card import QRCard
from app.models.user import User
from app.schemas.auth import AdminRegisterRequest, UserResponse
from app.schemas.membership import MembershipResponse

router = APIRouter()


# ── GET /admin/users ──────────────────────────────────────────────────────────
@router.get("/users", response_model=list[UserResponse])
async def list_users(
    response: Response,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    total = (await db.execute(select(func.count()).select_from(User))).scalar_one()
    result = await db.execute(
        select(User)
        .options(selectinload(User.memberships))
        .order_by(User.created_at.desc(), User.id.desc())
        .offset(skip)
        .limit(limit)
    )
    response.headers["X-Total-Count"] = str(total)
    users = result.scalars().all()
    return [UserResponse.from_orm_with_membership(u) for u in users]


# ── GET /admin/plans ──────────────────────────────────────────────────────────
class MembershipPlanResponse(BaseModel):
    id: int
    key: str
    type: str
    name: str
    amount: int
    duration_days: int
    is_active: bool

    model_config = {"from_attributes": True}

@router.get("/plans", response_model=list[MembershipPlanResponse])
async def list_plans(
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(MembershipPlan).where(MembershipPlan.is_active == True).order_by(MembershipPlan.duration_days)
    )
    return result.scalars().all()


# ── GET /admin/users/search ───────────────────────────────────────────────────
@router.get("/users/search", response_model=list[UserResponse])
async def search_users(
    q: str = Query(..., min_length=1),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(User)
        .options(selectinload(User.memberships))
        .where(
            or_(
                User.first_name.ilike(f"%{q}%"),
                User.last_name.ilike(f"%{q}%"),
                User.email.ilike(f"%{q}%"),
            )
        )
        .order_by(User.last_name, User.first_name)
    )
    users = result.scalars().all()
    return [UserResponse.from_orm_with_membership(u) for u in users]


# ── POST /admin/users ─────────────────────────────────────────────────────────
@router.post("/users", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
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
        first_name=body.first_name,
        last_name=body.last_name,
        email=body.email,
        phone_number=body.phone_number,
        age=body.age,
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
    plan_type: str
    start_date: str

@router.post("/users/{user_id}/memberships", response_model=MembershipResponse, status_code=status.HTTP_201_CREATED)
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

    plan_result = await db.execute(select(MembershipPlan).where(MembershipPlan.key == body.plan, MembershipPlan.type == body.plan_type, MembershipPlan.is_active == True))
    plan = plan_result.scalar_one_or_none()
    if not plan:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Plan '{body.plan}' invalid.")

    try:
        start = datetime.fromisoformat(body.start_date)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Format dată invalid. Folosește YYYY-MM-DD.")

    end = start + timedelta(days=plan.duration_days)

    membership = Membership(
        user_id=user.id,
        plan=body.plan,
        status="activ",
        amount=plan.amount,
        start_date=start,
        end_date=end,
    )
    db.add(membership)
    await db.flush()

    qr_code = f"CARD_{uuid.uuid4().hex[:12].upper()}"
    qr_card = QRCard(
        code=qr_code,
        type="digital",
        is_active=True,
        membership_id=membership.id,
    )
    db.add(qr_card)

    return membership


# ── PATCH /admin/memberships/{id} ────────────────────────────────────────────
class UpdateMembershipRequest(BaseModel):
    end_date: str

@router.patch("/memberships/{membership_id}", response_model=MembershipResponse)
async def update_membership(
    membership_id: int,
    body: UpdateMembershipRequest,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Membership).where(Membership.id == membership_id))
    membership = result.scalar_one_or_none()
    if not membership:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Membership not found.")

    try:
        membership.end_date = datetime.fromisoformat(body.end_date)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Format dată invalid. Folosește YYYY-MM-DD.")

    return membership


# ── PATCH /admin/users/{id}/deactivate ───────────────────────────────────────
@router.patch("/users/{user_id}/deactivate", response_model=UserResponse)
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
@router.patch("/users/{user_id}/activate", response_model=UserResponse)
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
