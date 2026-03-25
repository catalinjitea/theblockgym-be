from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_user, require_admin
from app.models.membership import Membership
from app.models.user import User

from app.schemas.membership import MembershipResponse

router = APIRouter()

# ── GET /memberships/me ───────────────────────────────────────────────────────
@router.get("/me", dependencies=[Depends(require_admin)], response_model=MembershipResponse | None)
async def get_my_membership(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Membership)
        .where(Membership.user_id == current_user.id, Membership.status == "activ")
        .order_by(Membership.created_at.desc())
    )
    return result.scalars().first()


# ── GET /memberships/me/history ───────────────────────────────────────────────
@router.get("/me/history", dependencies=[Depends(require_admin)], response_model=list[MembershipResponse])
async def get_my_membership_history(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Membership)
        .where(Membership.user_id == current_user.id)
        .order_by(Membership.created_at.desc())
    )
    return result.scalars().all()
