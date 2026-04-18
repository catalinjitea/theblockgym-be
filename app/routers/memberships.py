import io

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.core.dependencies import require_user
from app.models.membership import Membership
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

    png_bytes = generate_qr_image(membership.qr_card.code)
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
