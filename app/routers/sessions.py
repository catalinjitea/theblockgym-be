from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.core.dependencies import get_optional_user, require_trainer, require_user
from app.core.membership import count_sessions_used
from app.core.promo import is_free_class_session  # FREE-WEEK-PROMO
from app.core.timeutils import ro_now
from app.models.booking import Booking
from app.models.membership import Membership
from app.models.membership_plan import MembershipPlan
from app.models.session import Session
from app.models.user import User
from app.schemas.sessions import BookedUserResponse, BookingResponse, MySessionResponse, SessionResponse, TrainerSessionResponse

router = APIRouter()


async def _group_classes_booking_error(user: User, session: Session, db: AsyncSession) -> dict | None:
    """Check whether the user may book this session.

    Returns None when some active group_classes membership covers the
    session's date and still has sessions left in its quota
    (plan.sessions_count, counted per membership period; None = unlimited).
    During the free-classes week (app/core/promo.py) every registered user
    may book — no membership, no quota.
    Otherwise returns a {code, message} dict describing why not.
    """
    # ── FREE-WEEK-PROMO — delete this block after 2026-08-30 ──────────────────
    # Launch week is free for every registered user, membership or not.
    # Self-deactivating: once the week passes, no bookable session can start
    # inside the window, so the normal gate below takes over automatically.
    if is_free_class_session(session.start_datetime):
        return None
    # ── FREE-WEEK-PROMO END ────────────────────────────────────────────────────

    now = ro_now()
    memberships_result = await db.execute(
        select(Membership).where(
            Membership.user_id == user.id,
            Membership.status == "activ",
            Membership.end_date > now,
        )
    )
    memberships = memberships_result.scalars().all()

    group_plans: dict[str, MembershipPlan] = {}
    if memberships:
        plan_rows = await db.execute(
            select(MembershipPlan).where(
                MembershipPlan.key.in_({m.plan for m in memberships}),
                MembershipPlan.type == "group_classes",
            )
        )
        group_plans = {p.key: p for p in plan_rows.scalars().all()}

    has_plan = False
    covers_session = False
    for membership in memberships:
        plan = group_plans.get(membership.plan)
        if not plan:
            continue
        has_plan = True

        if not (membership.start_date <= session.start_datetime <= membership.end_date):
            continue
        covers_session = True

        if plan.sessions_count is None:
            return None

        used = await count_sessions_used(db, user.id, membership)
        if used < plan.sessions_count:
            return None

    if not has_plan:
        return {"code": "no_group_access", "message": "Abonamentul tău nu include acces la clase de grup."}
    if not covers_session:
        return {"code": "not_covered", "message": "Abonamentul tău nu acoperă data acestei sesiuni."}
    return {"code": "quota_exhausted", "message": "Ți-ai folosit toate ședințele incluse în abonament."}


# ── GET /sessions ─────────────────────────────────────────────────────────────
@router.get("/", response_model=list[SessionResponse])
async def list_sessions(
    current_user: Optional[User] = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
):
    now = ro_now()
    cutoff = now + timedelta(days=14)

    result = await db.execute(
        select(Session)
        .where(Session.start_datetime >= now, Session.start_datetime <= cutoff)
        .options(selectinload(Session.trainer))
        .order_by(Session.start_datetime)
    )
    sessions = result.scalars().all()
    session_ids = [s.id for s in sessions]

    counts: dict[int, int] = {}
    booked_ids: set[int] = set()
    if session_ids:
        count_rows = await db.execute(
            select(Booking.session_id, func.count(Booking.id))
            .where(Booking.session_id.in_(session_ids), Booking.status == "confirmed")
            .group_by(Booking.session_id)
        )
        counts = dict(count_rows.all())

        if current_user:
            booked_rows = await db.execute(
                select(Booking.session_id).where(
                    Booking.session_id.in_(session_ids),
                    Booking.user_id == current_user.id,
                    Booking.status == "confirmed",
                )
            )
            booked_ids = {session_id for (session_id,) in booked_rows.all()}

    return [
        SessionResponse(
            id=session.id,
            title=session.title,
            trainer_name=f"{session.trainer.first_name} {session.trainer.last_name}",
            start_datetime=session.start_datetime,
            duration_minutes=session.duration_minutes,
            max_capacity=session.max_capacity,
            booked_count=counts.get(session.id, 0),
            is_booked=session.id in booked_ids,
        )
        for session in sessions
    ]


# ── GET /sessions/my ──────────────────────────────────────────────────────────
@router.get("/my", response_model=list[MySessionResponse])
async def my_sessions(
    current_user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Booking)
        .where(Booking.user_id == current_user.id, Booking.status == "confirmed")
        .options(selectinload(Booking.session).selectinload(Session.trainer))
        .order_by(Booking.created_at.desc())
    )
    bookings = result.scalars().all()

    now = ro_now()
    upcoming = sorted(
        (b for b in bookings if b.session.start_datetime > now),
        key=lambda b: b.session.start_datetime,
    )
    # Most recent 20 past sessions, newest first, so history stays bounded
    past = sorted(
        (b for b in bookings if b.session.start_datetime <= now),
        key=lambda b: b.session.start_datetime,
        reverse=True,
    )[:20]

    return [
        MySessionResponse(
            booking_id=b.id,
            session_id=b.session.id,
            title=b.session.title,
            trainer_name=f"{b.session.trainer.first_name} {b.session.trainer.last_name}",
            start_datetime=b.session.start_datetime,
            duration_minutes=b.session.duration_minutes,
            status=b.status,
        )
        for b in upcoming + past
    ]


# ── GET /sessions/trainer ────────────────────────────────────────────────────
@router.get("/trainer", response_model=list[TrainerSessionResponse])
async def trainer_sessions(
    current_user: User = Depends(require_trainer),
    db: AsyncSession = Depends(get_db),
):
    # Include the last 14 days so trainers can review recent attendance
    cutoff = ro_now() - timedelta(days=14)
    result = await db.execute(
        select(Session)
        .where(Session.trainer_id == current_user.id, Session.start_datetime >= cutoff)
        .options(selectinload(Session.bookings).selectinload(Booking.user))
        .order_by(Session.start_datetime)
    )
    sessions = result.scalars().all()

    response = []
    for session in sessions:
        confirmed = [b for b in session.bookings if b.status == "confirmed"]
        response.append(TrainerSessionResponse(
            id=session.id,
            title=session.title,
            start_datetime=session.start_datetime,
            duration_minutes=session.duration_minutes,
            max_capacity=session.max_capacity,
            booked_count=len(confirmed),
            bookings=[BookedUserResponse(first_name=b.user.first_name, last_name=b.user.last_name) for b in confirmed],
        ))

    return response


# ── POST /sessions/{id}/book ──────────────────────────────────────────────────
@router.post("/{session_id}/book", response_model=BookingResponse, status_code=status.HTTP_201_CREATED)
async def book_session(
    session_id: int,
    current_user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    # Lock the session row so concurrent bookings serialize and the
    # capacity count below can't be raced past max_capacity
    session = await db.get(Session, session_id, with_for_update=True)
    if not session or session.start_datetime <= ro_now():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sesiunea nu există sau a început deja.")

    access_error = await _group_classes_booking_error(current_user, session, db)
    if access_error:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=access_error)

    # A cancelled booking keeps its row (unique on user_id + session_id),
    # so rebooking must revive it instead of inserting a duplicate
    existing = await db.scalar(
        select(Booking).where(
            Booking.session_id == session_id,
            Booking.user_id == current_user.id,
        )
    )
    if existing and existing.status == "confirmed":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ești deja înscris la această sesiune.")

    booked_count = await db.scalar(
        select(func.count(Booking.id)).where(
            Booking.session_id == session_id,
            Booking.status == "confirmed",
        )
    ) or 0
    if booked_count >= session.max_capacity:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Sesiunea este completă.")

    if existing:
        existing.status = "confirmed"
        existing.created_at = ro_now()
        await db.flush()
        return existing

    booking = Booking(user_id=current_user.id, session_id=session_id, status="confirmed")
    db.add(booking)
    await db.flush()
    return booking


# ── DELETE /sessions/{id}/book ────────────────────────────────────────────────
@router.delete("/{session_id}/book", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_booking(
    session_id: int,
    current_user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    booking = await db.scalar(
        select(Booking).where(
            Booking.session_id == session_id,
            Booking.user_id == current_user.id,
            Booking.status == "confirmed",
        )
    )
    if not booking:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rezervare negăsită.")

    session = await db.get(Session, session_id)
    if session and session.start_datetime <= ro_now():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Nu poți anula o sesiune care a început deja.")

    booking.status = "cancelled"
