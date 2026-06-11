import calendar
import math
import uuid
from datetime import date, datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from typing import Literal, Optional
from pydantic import BaseModel
from sqlalchemy import and_, extract, func, select, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.core.membership import compute_end_date
from app.core.dependencies import require_admin
from app.core.security import hash_password
from app.models.membership import Membership
from app.models.membership_plan import MembershipPlan
from app.models.qr_card import QRCard
from app.models.user import User
from app.core.email import send_welcome_email
from app.schemas.auth import AdminRegisterRequest, UserResponse
from app.schemas.membership import MembershipResponse

router = APIRouter()


# ── GET /admin/stats ──────────────────────────────────────────────────────────
class PlanCount(BaseModel):
    plan: str
    count: int

class MonthlyValue(BaseModel):
    month: str  # "YYYY-MM"
    value: int

class PlanTypeCount(BaseModel):
    type: str
    count: int

class StatsResponse(BaseModel):
    total_members: int
    active_subscriptions: int
    expired_subscription: int
    never_subscribed: int
    plan_distribution: list[PlanCount]
    expiring_tomorrow: int
    expiring_7_days: int
    plan_type_split: list[PlanTypeCount]

class PeriodStatsResponse(BaseModel):
    renewal_rate_pct: Optional[float]
    renewal_cohort_size: int
    renewal_renewed_count: int
    new_members_this_month: int
    renewed_this_month: int

@router.get("/stats", response_model=StatsResponse)
async def get_stats(
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    now = datetime.utcnow()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = now.replace(hour=23, minute=59, second=59)
    in_7_days = now + timedelta(days=7)

    total = (await db.execute(
        select(func.count()).select_from(User).where(User.is_admin == False)
    )).scalar_one()

    active_sub_filter = [
        Membership.start_date <= now,
        Membership.end_date >= now,
        User.is_admin == False,
    ]

    active_subscriptions = (await db.execute(
        select(func.count(func.distinct(Membership.user_id)))
        .join(User, User.id == Membership.user_id)
        .where(*active_sub_filter)
    )).scalar_one()

    active_user_ids_subq = (
        select(Membership.user_id)
        .where(Membership.start_date <= now, Membership.end_date >= now)
        .distinct()
    )
    expired_subscription = (await db.execute(
        select(func.count(func.distinct(Membership.user_id)))
        .join(User, User.id == Membership.user_id)
        .where(User.is_admin == False, ~Membership.user_id.in_(active_user_ids_subq))
    )).scalar_one()

    never_subscribed = (await db.execute(
        select(func.count())
        .select_from(User)
        .where(User.is_admin == False, ~User.id.in_(select(Membership.user_id).distinct()))
    )).scalar_one()

    plan_rows = (await db.execute(
        select(Membership.plan, func.count().label("count"))
        .join(User, User.id == Membership.user_id)
        .where(*active_sub_filter)
        .group_by(Membership.plan)
    )).all()

    expiring_tomorrow = (await db.execute(
        select(func.count(func.distinct(Membership.user_id)))
        .join(User, User.id == Membership.user_id)
        .where(*active_sub_filter, Membership.end_date >= today_start, Membership.end_date <= today_end)
    )).scalar_one()

    expiring_7 = (await db.execute(
        select(func.count(func.distinct(Membership.user_id)))
        .join(User, User.id == Membership.user_id)
        .where(*active_sub_filter, Membership.end_date <= in_7_days)
    )).scalar_one()

    plan_type_rows = (await db.execute(
        select(MembershipPlan.type, func.count(func.distinct(Membership.user_id)).label("count"))
        .join(MembershipPlan, and_(
            MembershipPlan.key == Membership.plan,
            MembershipPlan.amount == Membership.amount,
        ))
        .join(User, User.id == Membership.user_id)
        .where(*active_sub_filter)
        .group_by(MembershipPlan.type)
    )).all()

    return StatsResponse(
        total_members=total,
        active_subscriptions=active_subscriptions,
        expired_subscription=expired_subscription,
        never_subscribed=never_subscribed,
        plan_distribution=[PlanCount(plan=r.plan, count=r.count) for r in plan_rows],
        expiring_tomorrow=expiring_tomorrow,
        expiring_7_days=expiring_7,
        plan_type_split=[PlanTypeCount(type=r.type, count=r.count) for r in plan_type_rows],
    )


@router.get("/stats/period", response_model=PeriodStatsResponse)
async def get_period_stats(
    from_date: Optional[date] = Query(None),
    to_date: Optional[date] = Query(None),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    now = datetime.utcnow()
    period_end = datetime.combine(to_date, datetime.max.time().replace(microsecond=0)) if to_date else now

    if from_date:
        period_start = datetime.combine(from_date, datetime.min.time())
    else:
        today = now.date()
        prev18 = date(today.year - 1 if today.month == 1 else today.year, 12 if today.month == 1 else today.month - 1, 18)
        period_start = datetime.combine(prev18, datetime.min.time())

    # Renewal rate: compare equivalent days into each cycle.
    # e.g. if from_date=May 18 and today=May 26, compare Apr 18–Apr 26 expirations
    # against renewals that started May 18–May 26.
    effective_end = min(now, period_end)
    days_into = max((effective_end.date() - from_date).days, 0) if from_date else 0
    tomorrow_start = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)

    if from_date:
        prev_m = from_date.month - 1 if from_date.month > 1 else 12
        prev_y = from_date.year if from_date.month > 1 else from_date.year - 1
        prev_day = min(from_date.day, calendar.monthrange(prev_y, prev_m)[1])
        prev_cycle_start = datetime.combine(date(prev_y, prev_m, prev_day), datetime.min.time())
        prev_cycle_end = prev_cycle_start + timedelta(days=days_into)
        prev_cycle_end = prev_cycle_end.replace(hour=23, minute=59, second=59)
    else:
        prev_cycle_start = period_start
        prev_cycle_end = effective_end

    # Users who started a membership in the previous equivalent window and it expires today or earlier
    prev_cohort_subq = (
        select(Membership.user_id)
        .join(User, User.id == Membership.user_id)
        .where(
            Membership.start_date >= prev_cycle_start,
            Membership.start_date <= prev_cycle_end,
            Membership.end_date < tomorrow_start,
            User.is_admin == False,
        )
        .distinct()
    )
    total_prev_cohort = (await db.execute(
        select(func.count()).select_from(prev_cohort_subq.subquery())
    )).scalar_one()
    renewed_from_prev = (await db.execute(
        select(func.count(func.distinct(Membership.user_id)))
        .where(
            Membership.user_id.in_(prev_cohort_subq),
            Membership.start_date >= period_start,
            Membership.start_date <= effective_end,
        )
    )).scalar_one()
    renewal_rate_pct = round(renewed_from_prev / total_prev_cohort * 100, 1) if total_prev_cohort > 0 else None

    had_membership_before_period_subq = (
        select(Membership.user_id)
        .where(Membership.start_date < period_start)
        .distinct()
    )
    new_members_this_month = (await db.execute(
        select(func.count(func.distinct(Membership.user_id)))
        .join(User, User.id == Membership.user_id)
        .where(
            Membership.start_date >= period_start,
            Membership.start_date <= effective_end,
            User.is_admin == False,
            ~Membership.user_id.in_(had_membership_before_period_subq),
        )
    )).scalar_one()

    renewed_this_month = (await db.execute(
        select(func.count(func.distinct(Membership.user_id)))
        .join(User, User.id == Membership.user_id)
        .where(
            Membership.start_date >= period_start,
            Membership.start_date <= effective_end,
            User.is_admin == False,
            Membership.user_id.in_(had_membership_before_period_subq),
        )
    )).scalar_one()

    return PeriodStatsResponse(
        renewal_rate_pct=renewal_rate_pct,
        renewal_cohort_size=total_prev_cohort,
        renewal_renewed_count=renewed_from_prev,
        new_members_this_month=new_members_this_month,
        renewed_this_month=renewed_this_month,
    )


# ── GET /admin/stats/active-over-time ────────────────────────────────────────
@router.get("/stats/active-over-time", response_model=list[dict])
async def get_active_over_time(
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    now = datetime.utcnow()

    rows = (await db.execute(
        select(Membership.user_id, Membership.start_date, Membership.end_date)
        .join(User, User.id == Membership.user_id)
        .where(User.is_admin == False)
    )).all()

    if not rows:
        return []

    first_day = min(r.start_date for r in rows).replace(hour=0, minute=0, second=0, microsecond=0)

    result = []
    current = first_day
    while current.date() <= now.date():
        day_end = current.replace(hour=23, minute=59, second=59)
        check_point = min(day_end, now)
        active_count = len({r.user_id for r in rows if r.start_date <= check_point and r.end_date >= check_point})
        result.append({"period": current.strftime("%Y-%m-%d"), "value": active_count})
        current += timedelta(days=1)

    return result


# ── GET /admin/stats/registrations ───────────────────────────────────────────
class RegistrationPoint(BaseModel):
    period: str
    value: int

@router.get("/stats/registrations", response_model=list[RegistrationPoint])
async def get_registration_stats(
    granularity: Literal["day", "week", "month"] = Query("month"),
    from_date: Optional[date] = Query(None),
    to_date: Optional[date] = Query(None),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    now = datetime.utcnow()

    if granularity == "day":
        default_since = now - timedelta(days=30)
        trunc = "day"
    elif granularity == "week":
        default_since = now - timedelta(weeks=12)
        trunc = "week"
    else:
        default_since = now - timedelta(days=365)
        trunc = "month"

    since = datetime.combine(from_date, datetime.min.time()) if from_date else default_since
    until = datetime.combine(to_date, datetime.max.time().replace(microsecond=0)) if to_date else None

    filters = [Membership.created_at >= since]
    if until:
        filters.append(Membership.created_at <= until)

    period_col = func.date_trunc(trunc, Membership.created_at).label("period")
    rows = (await db.execute(
        select(period_col, func.count().label("count"))
        .where(*filters)
        .group_by(period_col)
        .order_by(period_col)
    )).all()

    result = []
    for r in rows:
        period_str = r.period.strftime("%Y-%m") if granularity == "month" else r.period.strftime("%Y-%m-%d")
        result.append(RegistrationPoint(period=period_str, value=r.count))
    return result


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
    max_freeze_days: Optional[int] = None
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

    await send_welcome_email(user.email, user.first_name)

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

    end = compute_end_date(start, plan)

    overlap_check = await db.execute(
        select(Membership).where(
            Membership.user_id == user_id,
            Membership.start_date < end,
            Membership.end_date > start,
        )
    )
    if overlap_check.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Data selectată se suprapune cu un abonament existent.",
        )

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

    # Check if the user currently has an active membership (advance purchase case).
    # If so, leave the existing QR card pointing to the current active membership —
    # verify_qr_card will auto-repoint it once that membership expires.
    has_active_membership = await db.execute(
        select(Membership).where(
            Membership.user_id == user_id,
            Membership.id != membership.id,
            Membership.end_date >= datetime.utcnow(),
        )
    )
    if not has_active_membership.scalar_one_or_none():
        # No currently active membership: reuse existing digital QR card or create a new one.
        existing_qr_result = await db.execute(
            select(QRCard)
            .join(Membership, QRCard.membership_id == Membership.id)
            .where(Membership.user_id == user_id, QRCard.type == "digital")
            .order_by(QRCard.created_at.desc())
            .limit(1)
        )
        existing_qr = existing_qr_result.scalar_one_or_none()

        if existing_qr:
            existing_qr.membership_id = membership.id
            existing_qr.is_active = True
        else:
            db.add(QRCard(
                code=f"QRCARD_{uuid.uuid4().hex[:12].upper()}",
                type="digital",
                is_active=True,
                membership_id=membership.id,
            ))

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
        parsed = datetime.fromisoformat(body.end_date)
        membership.end_date = parsed.replace(hour=23, minute=59, second=59, microsecond=0)
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


# ── PATCH /admin/users/{id} ───────────────────────────────────────────────────
class AdminUpdateUserRequest(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[str] = None
    phone_number: Optional[str] = None
    age: Optional[int] = None

@router.patch("/users/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: int,
    body: AdminUpdateUserRequest,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")

    if body.first_name is not None: user.first_name = body.first_name
    if body.last_name is not None: user.last_name = body.last_name
    if body.email is not None: user.email = body.email
    if body.phone_number is not None: user.phone_number = body.phone_number
    if body.age is not None: user.age = body.age

    return user


# ── POST /admin/memberships/{id}/freeze ──────────────────────────────────────
class FreezeMembershipRequest(BaseModel):
    freeze_days: int

@router.post("/memberships/{membership_id}/freeze", response_model=MembershipResponse)
async def freeze_membership(
    membership_id: int,
    body: FreezeMembershipRequest,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Membership).where(Membership.id == membership_id))
    membership = result.scalar_one_or_none()
    if not membership:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Membership not found.")

    now = datetime.utcnow()

    if membership.start_date > now or membership.end_date < now:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Abonamentul nu este activ.")

    if (membership.freeze_start is not None
            and membership.freeze_end is not None
            and membership.freeze_end > now):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Abonamentul este deja înghețat.")

    plan_result = await db.execute(
        select(MembershipPlan).where(
            MembershipPlan.key == membership.plan,
            MembershipPlan.amount == membership.amount,
        )
    )
    plan = plan_result.scalar_one_or_none()
    if not plan or not plan.max_freeze_days:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Planul nu permite înghețarea abonamentului.")

    if body.freeze_days < 1 or body.freeze_days > plan.max_freeze_days:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Perioada de îngheț trebuie să fie între 1 și {plan.max_freeze_days} zile.",
        )

    membership.freeze_start = now
    membership.freeze_end = now + timedelta(days=body.freeze_days)
    membership.end_date += timedelta(days=body.freeze_days)

    return membership


# ── POST /admin/memberships/{id}/unfreeze ─────────────────────────────────────
@router.post("/memberships/{membership_id}/unfreeze", response_model=MembershipResponse)
async def unfreeze_membership(
    membership_id: int,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Membership).where(Membership.id == membership_id))
    membership = result.scalar_one_or_none()
    if not membership:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Membership not found.")

    now = datetime.utcnow()

    if (membership.freeze_start is None
            or membership.freeze_end is None
            or membership.freeze_end <= now):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Abonamentul nu este înghețat.")

    remaining = membership.freeze_end - now
    membership.end_date -= remaining
    membership.freeze_end = now

    return membership


# ── PATCH /admin/users/{id}/password ─────────────────────────────────────────
class AdminChangePasswordRequest(BaseModel):
    new_password: str

@router.patch("/users/{user_id}/password", status_code=status.HTTP_204_NO_CONTENT)
async def admin_change_user_password(
    user_id: int,
    body: AdminChangePasswordRequest,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    if len(body.new_password) < 8:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Parola trebuie să aibă cel puțin 8 caractere.")
    user.hashed_password = hash_password(body.new_password)
    await db.commit()
