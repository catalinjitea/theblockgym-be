from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import Depends

from app.core.database import get_db
from app.models.membership_plan import MembershipPlan

router = APIRouter()


class PlanResponse(BaseModel):
    key: str
    type: str
    name: str
    amount: int
    duration_days: int

    model_config = {"from_attributes": True}


# ── GET /plans ────────────────────────────────────────────────────────────────
@router.get("", response_model=list[PlanResponse])
async def list_plans(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(MembershipPlan)
        .where(MembershipPlan.is_active == True)
        .order_by(MembershipPlan.type, MembershipPlan.duration_days)
    )
    return result.scalars().all()
