import calendar
import math
import os
import uuid
from datetime import date, datetime, time, timedelta
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from typing import Literal, Optional
from pydantic import BaseModel
from sqlalchemy import and_, delete as sa_delete, extract, func, select, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.core.membership import (
    apply_freeze,
    available_freeze_days,
    cancel_freeze,
    compute_end_date,
    load_membership_for_update,
    snapshot_freeze_allowance,
)
from app.core.promo import MAX_DISCOUNT_PERCENT, normalize_code
from app.core.dependencies import require_admin
from app.core.security import create_unsubscribe_token, hash_password
from app.models.membership import Membership
from app.models.membership_plan import MembershipPlan
from app.models.promo_code import (
    AUDIENCE_EVERYONE, AUDIENCE_NAMED, AUDIENCES,
    PromoCode, PromoCodeUser, PromoRedemption,
)
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
    # Defaults cover the whole current calendar month.
    period_from = from_date or now.date().replace(day=1)
    period_to = to_date or period_from.replace(
        day=calendar.monthrange(period_from.year, period_from.month)[1]
    )
    period_start = datetime.combine(period_from, datetime.min.time())
    period_end = datetime.combine(period_to, datetime.max.time().replace(microsecond=0))

    # Renewal rate: compare equivalent days into each cycle.
    # e.g. if period_from=May 1 and today=May 9, compare Apr 1–Apr 9 expirations
    # against renewals that started May 1–May 9.
    effective_end = min(now, period_end)
    days_into = max((effective_end.date() - period_from).days, 0)
    tomorrow_start = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)

    prev_m = period_from.month - 1 if period_from.month > 1 else 12
    prev_y = period_from.year if period_from.month > 1 else period_from.year - 1
    prev_day = min(period_from.day, calendar.monthrange(prev_y, prev_m)[1])
    prev_cycle_start = datetime.combine(date(prev_y, prev_m, prev_day), datetime.min.time())
    # Never let the comparison window spill into the current period (e.g. a 31-day
    # month measured against a 28-day February).
    prev_cycle_end = min(
        prev_cycle_start + timedelta(days=days_into),
        datetime.combine(period_from - timedelta(days=1), datetime.min.time()),
    ).replace(hour=23, minute=59, second=59)

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
    words = q.split()
    result = await db.execute(
        select(User)
        .options(selectinload(User.memberships))
        .where(
            and_(
                *[
                    or_(
                        User.first_name.ilike(f"%{word}%"),
                        User.last_name.ilike(f"%{word}%"),
                        User.email.ilike(f"%{word}%"),
                    )
                    for word in words
                ]
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
        **snapshot_freeze_allowance(plan),
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
    freeze_start: date
    freeze_end: date

@router.post("/memberships/{membership_id}/freeze", response_model=MembershipResponse)
async def freeze_membership(
    membership_id: int,
    body: FreezeMembershipRequest,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    membership = await load_membership_for_update(db, membership_id)
    if not membership:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Membership not found.")

    apply_freeze(membership, body.freeze_start, body.freeze_end)

    response = MembershipResponse.model_validate(membership)
    response.max_freeze_days = available_freeze_days(membership)
    return response


# ── POST /admin/memberships/{id}/unfreeze ─────────────────────────────────────
@router.post("/memberships/{membership_id}/unfreeze", response_model=MembershipResponse)
async def unfreeze_membership(
    membership_id: int,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    membership = await load_membership_for_update(db, membership_id)
    if not membership:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Membership not found.")

    cancel_freeze(membership)

    response = MembershipResponse.model_validate(membership)
    response.max_freeze_days = available_freeze_days(membership)
    return response


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


# ── GET /admin/marketing/export-csv ──────────────────────────────────────────
@router.get("/marketing/export-csv", dependencies=[Depends(require_admin)])
async def export_marketing_csv(
    audience: Literal["all", "no_membership", "expired"] = Query("all"),
    db: AsyncSession = Depends(get_db),
):
    frontend_url = os.getenv("FRONTEND_URL", "http://localhost:3000")

    if audience == "no_membership":
        stmt = select(User).where(
            User.marketing_unsubscribed == False,
            ~User.memberships.any(),
        )
    elif audience == "expired":
        stmt = select(User).where(
            User.marketing_unsubscribed == False,
            User.memberships.any(Membership.end_date < datetime.utcnow()),
            ~User.memberships.any(Membership.end_date >= datetime.utcnow()),
        )
    else:
        stmt = select(User)

    result = await db.execute(stmt)
    users = result.scalars().all()

    rows = ["email,unsubscribed,unsubscribe_url"]
    for user in users:
        token = create_unsubscribe_token(user.id)
        url = f"{frontend_url}/unsubscribe?token={token}"
        rows.append(f"{user.email},{str(user.marketing_unsubscribed).lower()},{url}")

    csv_content = "\n".join(rows)
    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="marketing_{audience}.csv"'},
    )


# ── Promo codes ───────────────────────────────────────────────────────────────
class PromoCodeUserSummary(BaseModel):
    user_id: int
    first_name: str
    last_name: str
    email: str


class PromoCodeResponse(BaseModel):
    id: int
    code: str
    discount_percent: int
    audience: str
    max_uses: Optional[int]
    max_uses_per_user: int
    valid_from: Optional[datetime]
    valid_until: Optional[datetime]
    plan_key: Optional[str]
    plan_type: Optional[str]
    is_active: bool
    created_at: datetime
    times_used: int                       # confirmed redemptions only
    allowed_users: list[PromoCodeUserSummary]


class PromoCodeCreateRequest(BaseModel):
    code: str
    discount_percent: int
    audience: str = AUDIENCE_NAMED
    max_uses: Optional[int] = None
    max_uses_per_user: int = 1
    valid_from: Optional[datetime] = None
    valid_until: Optional[datetime] = None
    plan_key: Optional[str] = None
    plan_type: Optional[str] = None
    allowed_user_ids: list[int] = []


class PromoCodeUpdateRequest(BaseModel):
    discount_percent: Optional[int] = None
    audience: Optional[str] = None
    max_uses: Optional[int] = None
    max_uses_per_user: Optional[int] = None
    valid_from: Optional[datetime] = None
    valid_until: Optional[datetime] = None
    plan_key: Optional[str] = None
    plan_type: Optional[str] = None
    is_active: Optional[bool] = None
    allowed_user_ids: Optional[list[int]] = None


async def _serialize_promo_code(db: AsyncSession, promo: PromoCode) -> PromoCodeResponse:
    confirmed = (await db.execute(
        select(func.count()).select_from(PromoRedemption).where(
            PromoRedemption.promo_code_id == promo.id,
            PromoRedemption.status == "confirmed",
        )
    )).scalar_one()
    return PromoCodeResponse(
        id=promo.id,
        code=promo.code,
        discount_percent=promo.discount_percent,
        audience=promo.audience,
        max_uses=promo.max_uses,
        max_uses_per_user=promo.max_uses_per_user,
        valid_from=promo.valid_from,
        valid_until=promo.valid_until,
        plan_key=promo.plan_key,
        plan_type=promo.plan_type,
        is_active=promo.is_active,
        created_at=promo.created_at,
        times_used=confirmed,
        allowed_users=[
            PromoCodeUserSummary(
                user_id=entry.user.id,
                first_name=entry.user.first_name,
                last_name=entry.user.last_name,
                email=entry.user.email,
            )
            for entry in promo.allowed_users
        ],
    )


def _validate_promo_fields(discount_percent: Optional[int], max_uses: Optional[int],
                           max_uses_per_user: Optional[int],
                           valid_from: Optional[datetime], valid_until: Optional[datetime]) -> None:
    if discount_percent is not None and not (1 <= discount_percent <= MAX_DISCOUNT_PERCENT):
        raise HTTPException(
            status_code=400,
            detail=f"Reducerea trebuie să fie între 1 și {MAX_DISCOUNT_PERCENT}%. "
                   "Pentru abonamente gratuite folosește atribuirea manuală.",
        )
    if max_uses is not None and max_uses < 1:
        raise HTTPException(status_code=400, detail="Numărul maxim de utilizări trebuie să fie cel puțin 1.")
    if max_uses_per_user is not None and max_uses_per_user < 1:
        raise HTTPException(status_code=400, detail="Numărul de utilizări per membru trebuie să fie cel puțin 1.")
    if valid_from and valid_until and valid_until <= valid_from:
        raise HTTPException(status_code=400, detail="Data de expirare trebuie să fie după data de început.")


def _validate_audience(audience: str, allowed_user_ids: Optional[list[int]]) -> None:
    """Audience and allowlist must agree, or the code silently reaches the wrong
    people — a "named" code with nobody named reaches no one, and an "everyone"
    code carrying an allowlist reads as restricted while behaving as public."""
    if audience not in AUDIENCES:
        raise HTTPException(status_code=400, detail=f"Audiență invalidă: '{audience}'.")
    if audience == AUDIENCE_NAMED and not allowed_user_ids:
        raise HTTPException(
            status_code=400,
            detail="Un cod pentru membri selectați are nevoie de cel puțin un membru. "
                   "Pentru un cod public alege „Toți membrii”.",
        )
    if audience == AUDIENCE_EVERYONE and allowed_user_ids:
        raise HTTPException(
            status_code=400,
            detail="Un cod public nu poate avea listă de membri. "
                   "Alege „Doar membri selectați” dacă vrei să îl restricționezi.",
        )


async def _load_promo(db: AsyncSession, promo_id: int) -> PromoCode:
    promo = (await db.execute(
        select(PromoCode)
        .where(PromoCode.id == promo_id)
        .options(selectinload(PromoCode.allowed_users).selectinload(PromoCodeUser.user))
    )).scalar_one_or_none()
    if not promo:
        raise HTTPException(status_code=404, detail="Codul de reducere nu a fost găsit.")
    return promo


@router.get("/promo-codes", response_model=list[PromoCodeResponse])
async def list_promo_codes(
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    promos = (await db.execute(
        select(PromoCode)
        .options(selectinload(PromoCode.allowed_users).selectinload(PromoCodeUser.user))
        .order_by(PromoCode.created_at.desc())
    )).scalars().all()
    return [await _serialize_promo_code(db, p) for p in promos]


@router.post("/promo-codes", response_model=PromoCodeResponse, status_code=status.HTTP_201_CREATED)
async def create_promo_code(
    body: PromoCodeCreateRequest,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    code = normalize_code(body.code)
    if not code:
        raise HTTPException(status_code=400, detail="Codul nu poate fi gol.")
    _validate_promo_fields(body.discount_percent, body.max_uses, body.max_uses_per_user,
                           body.valid_from, body.valid_until)
    _validate_audience(body.audience, body.allowed_user_ids)

    existing = (await db.execute(select(PromoCode).where(PromoCode.code == code))).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=400, detail=f"Codul '{code}' există deja.")

    promo = PromoCode(
        code=code,
        discount_percent=body.discount_percent,
        audience=body.audience,
        max_uses=body.max_uses,
        max_uses_per_user=body.max_uses_per_user,
        valid_from=body.valid_from,
        valid_until=body.valid_until,
        plan_key=body.plan_key or None,
        plan_type=body.plan_type or None,
    )
    db.add(promo)
    await db.flush()

    await _set_allowlist(db, promo, body.allowed_user_ids)
    return await _serialize_promo_code(db, await _load_promo(db, promo.id))


async def _set_allowlist(db: AsyncSession, promo: PromoCode, user_ids: list[int]) -> None:
    """Replace the code's allowlist. An empty list opens the code to everyone."""
    unique_ids = list(dict.fromkeys(user_ids))
    if unique_ids:
        found = (await db.execute(select(User.id).where(User.id.in_(unique_ids)))).scalars().all()
        missing = set(unique_ids) - set(found)
        if missing:
            raise HTTPException(status_code=400, detail=f"Utilizatori inexistenți: {sorted(missing)}")

    await db.execute(sa_delete(PromoCodeUser).where(PromoCodeUser.promo_code_id == promo.id))
    for user_id in unique_ids:
        db.add(PromoCodeUser(promo_code_id=promo.id, user_id=user_id))
    await db.flush()

    # The bulk delete goes straight to the DB without touching the session, so
    # promo.allowed_users is still holding the rows we just removed. Expire that
    # one attribute — expiring the whole object would drop the loaded PK too and
    # send the caller's reload into a sync lazy-load — or the caller serialises
    # the old allowlist back out.
    db.expire(promo, ["allowed_users"])


@router.patch("/promo-codes/{promo_id}", response_model=PromoCodeResponse)
async def update_promo_code(
    promo_id: int,
    body: PromoCodeUpdateRequest,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    promo = await _load_promo(db, promo_id)
    _validate_audience(
        body.audience if body.audience is not None else promo.audience,
        body.allowed_user_ids if body.allowed_user_ids is not None
        else [entry.user_id for entry in promo.allowed_users],
    )
    _validate_promo_fields(
        body.discount_percent,
        body.max_uses,
        body.max_uses_per_user,
        body.valid_from if body.valid_from is not None else promo.valid_from,
        body.valid_until if body.valid_until is not None else promo.valid_until,
    )

    # Editing a live code never rewrites history — past redemptions carry their
    # own snapshot of the percent and amounts they were granted at.
    #
    # Keyed off model_fields_set rather than a None check, so that explicitly
    # sending null clears a limit (max_uses -> unlimited, valid_until -> never
    # expires, plan_key -> every plan) while an omitted field stays untouched.
    provided = body.model_fields_set
    clearable = {"max_uses", "valid_from", "valid_until", "plan_key", "plan_type"}
    for field in ("discount_percent", "audience", "max_uses", "max_uses_per_user",
                  "valid_from", "valid_until", "plan_key", "plan_type", "is_active"):
        if field not in provided:
            continue
        value = getattr(body, field)
        if value is None and field not in clearable:
            continue
        setattr(promo, field, value or None if field in {"plan_key", "plan_type"} else value)

    if body.allowed_user_ids is not None:
        await _set_allowlist(db, promo, body.allowed_user_ids)

    await db.flush()
    return await _serialize_promo_code(db, await _load_promo(db, promo_id))


@router.delete("/promo-codes/{promo_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_promo_code(
    promo_id: int,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    promo = await _load_promo(db, promo_id)
    confirmed = (await db.execute(
        select(func.count()).select_from(PromoRedemption).where(
            PromoRedemption.promo_code_id == promo.id,
            PromoRedemption.status == "confirmed",
        )
    )).scalar_one()
    # Deleting would cascade the redemptions away with it, losing the record of
    # discounts already granted. Deactivate instead once a code has been used.
    if confirmed:
        raise HTTPException(
            status_code=400,
            detail="Codul a fost deja folosit și nu poate fi șters. Dezactivează-l în schimb.",
        )
    await db.delete(promo)


class PromoRedemptionResponse(BaseModel):
    id: int
    order_id: str
    code: str
    user_id: int
    member_name: str
    discount_percent: int
    original_amount: int
    discount_amount: int
    final_amount: int
    status: str
    created_at: datetime
    confirmed_at: Optional[datetime]


@router.get("/promo-redemptions", response_model=list[PromoRedemptionResponse])
async def list_promo_redemptions(
    response: Response,
    promo_id: Optional[int] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    filters = [PromoRedemption.status == "confirmed"]
    if promo_id is not None:
        filters.append(PromoRedemption.promo_code_id == promo_id)

    total = (await db.execute(
        select(func.count()).select_from(PromoRedemption).where(*filters)
    )).scalar_one()

    rows = (await db.execute(
        select(PromoRedemption, PromoCode.code, User.first_name, User.last_name)
        .join(PromoCode, PromoCode.id == PromoRedemption.promo_code_id)
        .join(User, User.id == PromoRedemption.user_id)
        .where(*filters)
        .order_by(PromoRedemption.confirmed_at.desc(), PromoRedemption.id.desc())
        .offset(skip)
        .limit(limit)
    )).all()

    response.headers["X-Total-Count"] = str(total)
    return [
        PromoRedemptionResponse(
            id=r.id,
            order_id=r.order_id,
            code=code,
            user_id=r.user_id,
            member_name=f"{first_name} {last_name}",
            discount_percent=r.discount_percent,
            original_amount=r.original_amount,
            discount_amount=r.discount_amount,
            final_amount=r.final_amount,
            status=r.status,
            created_at=r.created_at,
            confirmed_at=r.confirmed_at,
        )
        for r, code, first_name, last_name in rows
    ]
