import io
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.core.dependencies import require_user
from app.core.membership import (
    apply_freeze,
    available_freeze_days,
    cancel_freeze,
    load_active_membership_for_update,
)
from app.models.membership import Membership
from app.models.membership_plan import MembershipPlan
from app.models.qr_card import QRCard
from app.models.user import User
from app.routers.qr_cards import generate_qr_image
from app.schemas.membership import MembershipResponse

router = APIRouter()


async def _plans_by_key(db: AsyncSession, keys: set[str]) -> dict[str, list[MembershipPlan]]:
    if not keys:
        return {}
    result = await db.execute(select(MembershipPlan).where(MembershipPlan.key.in_(keys)))
    by_key: dict[str, list[MembershipPlan]] = {}
    for plan in result.scalars().all():
        by_key.setdefault(plan.key, []).append(plan)
    return by_key


def _pick_plan(plans: list[MembershipPlan] | None, amount: int) -> MembershipPlan | None:
    """Plan row for a membership. Keys are not unique across plan types,
    so prefer the row matching what the member actually paid."""
    if not plans:
        return None
    return next((p for p in plans if p.amount == amount), plans[0])


async def _resolve_plan(db: AsyncSession, plan_key: str, amount: int) -> MembershipPlan | None:
    return _pick_plan((await _plans_by_key(db, {plan_key})).get(plan_key), amount)


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
    membership = result.scalars().first()
    if not membership:
        return None
    response = MembershipResponse.model_validate(membership)
    response.max_freeze_days = available_freeze_days(membership)

    plan = await _resolve_plan(db, membership.plan, membership.amount)
    response.plan_name = plan.name if plan else None

    return response


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
    memberships = result.scalars().all()
    plans_by_key = await _plans_by_key(db, {m.plan for m in memberships})
    responses = []
    for membership in memberships:
        response = MembershipResponse.model_validate(membership)
        response.max_freeze_days = available_freeze_days(membership)
        plan = _pick_plan(plans_by_key.get(membership.plan), membership.amount)
        response.plan_name = plan.name if plan else None
        responses.append(response)
    return responses


# ── POST /memberships/me/freeze ───────────────────────────────────────────────
class FreezeMembershipRequest(BaseModel):
    freeze_start: date
    freeze_end: date

@router.post("/me/freeze", response_model=MembershipResponse)
async def freeze_my_membership(
    body: FreezeMembershipRequest,
    current_user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    membership = await load_active_membership_for_update(db, current_user.id)
    if not membership:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Nu există un abonament activ.")

    apply_freeze(membership, body.freeze_start, body.freeze_end)

    response = MembershipResponse.model_validate(membership)
    response.max_freeze_days = available_freeze_days(membership)
    return response


# ── POST /memberships/me/unfreeze ─────────────────────────────────────────────
@router.post("/me/unfreeze", response_model=MembershipResponse)
async def unfreeze_my_membership(
    current_user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    membership = await load_active_membership_for_update(db, current_user.id)
    if not membership:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Nu există un abonament activ.")

    cancel_freeze(membership)

    response = MembershipResponse.model_validate(membership)
    response.max_freeze_days = available_freeze_days(membership)
    return response
