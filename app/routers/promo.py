from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import require_user
from app.core.promo import has_eligible_code, quote_promo_code
from app.models.membership_plan import MembershipPlan
from app.models.user import User

router = APIRouter()


class ValidatePromoRequest(BaseModel):
    code: str
    plan: str
    plan_type: str = "full_time"


class ValidatePromoResponse(BaseModel):
    code: str
    discount_percent: int
    original_amount: int
    discount_amount: int
    final_amount: int


# ── POST /promo/validate ──────────────────────────────────────────────────────
# Preview only, so the checkout page can show the discounted price before the
# customer commits. Deliberately does NOT reserve the code — the reservation is
# the pending redemption row written by create-checkout-session. Checkout
# re-validates under a row lock, so a code passing here can still be refused
# there if it runs out in between.
@router.post("/validate", response_model=ValidatePromoResponse)
async def validate_promo(
    body: ValidatePromoRequest,
    current_user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    plan = (await db.execute(
        select(MembershipPlan).where(
            MembershipPlan.key == body.plan,
            MembershipPlan.type == body.plan_type,
            MembershipPlan.is_active == True,
        )
    )).scalar_one_or_none()
    if not plan:
        raise HTTPException(status_code=400, detail=f"Plan '{body.plan}' ({body.plan_type}) not found.")

    quote = await quote_promo_code(db, body.code, current_user, plan)

    return ValidatePromoResponse(
        code=quote.promo_code.code,
        discount_percent=quote.discount_percent,
        original_amount=quote.original_amount,
        discount_amount=quote.discount_amount,
        final_amount=quote.final_amount,
    )


class PromoAvailabilityResponse(BaseModel):
    available: bool


# ── GET /promo/available ──────────────────────────────────────────────────────
# Whether to render the discount field at all. Members with nothing available
# never learn that discount codes exist. Returns a bare boolean and never a
# code, so it gives away nothing guessable.
@router.get("/available", response_model=PromoAvailabilityResponse)
async def promo_available(
    plan: str = Query(...),
    plan_type: str = Query("full_time"),
    current_user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    membership_plan = (await db.execute(
        select(MembershipPlan).where(
            MembershipPlan.key == plan,
            MembershipPlan.type == plan_type,
            MembershipPlan.is_active == True,
        )
    )).scalar_one_or_none()
    if not membership_plan:
        return PromoAvailabilityResponse(available=False)

    return PromoAvailabilityResponse(
        available=await has_eligible_code(db, current_user, membership_plan)
    )
