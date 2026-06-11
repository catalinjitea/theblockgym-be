import io
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.core.dependencies import require_user
from app.models.membership import Membership
from app.models.membership_plan import MembershipPlan
from app.models.qr_card import QRCard
from app.models.user import User
from app.routers.qr_cards import generate_qr_image
from app.schemas.membership import MembershipResponse

router = APIRouter()

# ── GET /memberships/me ───────────────────────────────────────────────────────
@router.get("/me", response_model=MembershipResponse | None)
async def get_my_membership(
    current_user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Membership)
        .where(Membership.user_id == current_user.id, Membership.status == "activ")
        .order_by(Membership.created_at.desc())
    )
    return result.scalars().first()


# ── GET /memberships/me/qr ────────────────────────────────────────────────────
@router.get("/me/qr")
async def get_my_qr(
    current_user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Membership)
        .where(Membership.user_id == current_user.id, Membership.status == "activ")
        .options(selectinload(Membership.qr_card))
        .order_by(Membership.created_at.desc())
    )
    membership = result.scalars().first()

    if not membership or not membership.qr_card:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No QR code found for your membership.")

    code = membership.qr_card.code
    if code.startswith("QR"):
        code = code[2:]
    png_bytes = generate_qr_image(code)
    return StreamingResponse(io.BytesIO(png_bytes), media_type="image/png")


# ── GET /memberships/me/history ───────────────────────────────────────────────
@router.get("/me/history", response_model=list[MembershipResponse])
async def get_my_membership_history(
    current_user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Membership)
        .where(Membership.user_id == current_user.id)
        .order_by(Membership.created_at.desc())
    )
    return result.scalars().all()


# ── POST /memberships/me/freeze ───────────────────────────────────────────────
class FreezeMembershipRequest(BaseModel):
    freeze_days: int

@router.post("/me/freeze", response_model=MembershipResponse)
async def freeze_my_membership(
    body: FreezeMembershipRequest,
    current_user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Membership)
        .where(Membership.user_id == current_user.id, Membership.status == "activ")
        .order_by(Membership.created_at.desc())
    )
    membership = result.scalars().first()

    now = datetime.utcnow()

    if not membership or membership.start_date > now or membership.end_date < now:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Nu există un abonament activ.")

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


# ── POST /memberships/me/unfreeze ─────────────────────────────────────────────
@router.post("/me/unfreeze", response_model=MembershipResponse)
async def unfreeze_my_membership(
    current_user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Membership)
        .where(Membership.user_id == current_user.id, Membership.status == "activ")
        .order_by(Membership.created_at.desc())
    )
    membership = result.scalars().first()

    now = datetime.utcnow()

    if not membership:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Nu există un abonament activ.")

    if (membership.freeze_start is None
            or membership.freeze_end is None
            or membership.freeze_end <= now):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Abonamentul nu este înghețat.")

    remaining = membership.freeze_end - now
    membership.end_date -= remaining
    membership.freeze_end = now

    return membership
