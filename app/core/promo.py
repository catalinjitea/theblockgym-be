"""Discount-code validation and pricing.

Shared by `POST /promo/validate` (preview, read-only) and
`POST /payments/create-checkout-session` (authoritative, locking) so the price
quoted to the customer can never drift from the price they are charged.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.membership_plan import MembershipPlan
from app.models.promo_code import (
    AUDIENCE_EVERYONE, AUDIENCE_NAMED, PromoCode, PromoCodeUser, PromoRedemption,
)
from app.models.user import User

# A checkout that never gets paid holds its slot against max_uses for this long,
# then frees it. Without the hold, two people racing on a single-use code could
# both be charged; without the expiry, one abandoned checkout would burn it
# forever.
PENDING_HOLD_MINUTES = 30

# Netopia rejects trivially small charges, so a discount may not take an order
# below this. Only reachable on cheap plans at a near-maximum percentage.
MIN_CHARGE_BANI = 100

MAX_DISCOUNT_PERCENT = 99

# Marks a 400 as "the discount code was refused", so the checkout page can show
# it against the code field instead of treating it as a general failure.
PROMO_ERROR_CODE = "promo_rejected"


@dataclass
class PromoQuote:
    promo_code: PromoCode
    discount_percent: int
    original_amount: int   # bani
    discount_amount: int   # bani
    final_amount: int      # bani


def normalize_code(raw: str) -> str:
    return raw.strip().upper()


def compute_amounts(original_amount: int, discount_percent: int) -> tuple[int, int]:
    """Return (discount_amount, final_amount) in bani.

    Floored, so the discount never rounds up in the customer's favour by a ban
    and — more importantly — so `/promo/validate` and checkout always agree.
    """
    discount = original_amount * discount_percent // 100
    return discount, original_amount - discount


def _usage_filter(code_id: int):
    """Redemptions that count against a code's limits: confirmed, plus pending
    ones still inside their hold window."""
    cutoff = datetime.utcnow() - timedelta(minutes=PENDING_HOLD_MINUTES)
    return [
        PromoRedemption.promo_code_id == code_id,
        or_(
            PromoRedemption.status == "confirmed",
            and_(PromoRedemption.status == "pending",
                 PromoRedemption.created_at >= cutoff),
        ),
    ]


async def count_uses(
    db: AsyncSession,
    code_id: int,
    user_id: Optional[int] = None,
    exclude_pending_user_id: Optional[int] = None,
) -> int:
    """Uses counted against a code's limits.

    `exclude_pending_user_id` drops that user's own unpaid holds from the tally.
    A hold exists because the member is mid-checkout, so it should reserve the
    slot against *other* people while never locking its owner out of retrying an
    abandoned attempt — which would otherwise hide the code from them for
    PENDING_HOLD_MINUTES.
    """
    conditions = _usage_filter(code_id)
    if user_id is not None:
        conditions.append(PromoRedemption.user_id == user_id)
    if exclude_pending_user_id is not None:
        conditions.append(~and_(
            PromoRedemption.user_id == exclude_pending_user_id,
            PromoRedemption.status == "pending",
        ))
    return (await db.execute(
        select(func.count()).select_from(PromoRedemption).where(*conditions)
    )).scalar_one()


async def release_own_pending_holds(db: AsyncSession, raw_code: str, user_id: int) -> None:
    """Drop a member's unpaid holds on a code before they start a fresh checkout,
    so repeated attempts replace their reservation instead of stacking up."""
    promo_id = (await db.execute(
        select(PromoCode.id).where(func.upper(PromoCode.code) == normalize_code(raw_code))
    )).scalar_one_or_none()
    if promo_id is None:
        return
    await db.execute(delete(PromoRedemption).where(
        PromoRedemption.promo_code_id == promo_id,
        PromoRedemption.user_id == user_id,
        PromoRedemption.status == "pending",
    ))


async def quote_promo_code(
    db: AsyncSession,
    raw_code: str,
    user: User,
    plan: MembershipPlan,
    *,
    lock: bool = False,
) -> PromoQuote:
    """Validate `raw_code` for this user and plan, or raise HTTPException(400).

    Pass `lock=True` from checkout: it takes a row lock on the code so two
    concurrent checkouts cannot both pass a max_uses check that only one of
    them fits under.
    """
    code = normalize_code(raw_code)

    query = select(PromoCode).where(func.upper(PromoCode.code) == code)
    if lock:
        query = query.with_for_update()
    promo = (await db.execute(query)).scalar_one_or_none()

    def reject(message: str) -> None:
        # Tagged so checkout can tell a rejected code apart from an unrelated
        # failure (date overlap, gateway error) and not blame the code for it.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": PROMO_ERROR_CODE, "message": message},
        )

    if not promo or not promo.is_active:
        reject("Codul de reducere nu este valid.")

    now = datetime.utcnow()
    if promo.valid_from and now < promo.valid_from:
        reject("Codul de reducere nu este încă activ.")
    if promo.valid_until and now >= promo.valid_until:
        reject("Codul de reducere a expirat.")

    if promo.plan_key and promo.plan_key != plan.key:
        reject("Codul de reducere nu se aplică acestui abonament.")
    if promo.plan_type and promo.plan_type != plan.type:
        reject("Codul de reducere nu se aplică acestui tip de abonament.")

    # Who the code is for is stated on the code itself. A "named" code with an
    # empty allowlist reaches nobody — which is correct, and visible in the data,
    # rather than silently reaching everyone.
    if promo.audience == AUDIENCE_NAMED:
        permitted = (await db.execute(
            select(PromoCodeUser.id).where(
                PromoCodeUser.promo_code_id == promo.id,
                PromoCodeUser.user_id == user.id,
            )
        )).scalar_one_or_none()
        if not permitted:
            reject("Codul de reducere nu este disponibil pentru contul tău.")

    if promo.max_uses is not None:
        if await count_uses(db, promo.id, exclude_pending_user_id=user.id) >= promo.max_uses:
            reject("Codul de reducere a atins numărul maxim de utilizări.")

    if await count_uses(db, promo.id, user_id=user.id,
                        exclude_pending_user_id=user.id) >= promo.max_uses_per_user:
        reject("Ai folosit deja acest cod de reducere.")

    discount_amount, final_amount = compute_amounts(plan.amount, promo.discount_percent)
    if final_amount < MIN_CHARGE_BANI:
        reject("Codul de reducere nu poate fi aplicat acestui abonament.")

    return PromoQuote(
        promo_code=promo,
        discount_percent=promo.discount_percent,
        original_amount=plan.amount,
        discount_amount=discount_amount,
        final_amount=final_amount,
    )


async def has_eligible_code(db: AsyncSession, user: User, plan: MembershipPlan) -> bool:
    """True when this member could redeem at least one code against this plan.

    Backs the checkout page's decision to render the discount field at all: a
    member with nothing available is never shown that discounts exist. It answers
    only yes/no and never returns a code, so it reveals nothing a member could
    use to guess one.

    Deliberately mirrors quote_promo_code's rules — anything accepted here must
    survive validation at checkout, or the field appears and then refuses to work.
    """
    now = datetime.utcnow()
    candidates = (await db.execute(
        select(PromoCode).where(
            PromoCode.is_active == True,
            or_(PromoCode.valid_from.is_(None), PromoCode.valid_from <= now),
            or_(PromoCode.valid_until.is_(None), PromoCode.valid_until > now),
            or_(PromoCode.plan_key.is_(None), PromoCode.plan_key == plan.key),
            or_(PromoCode.plan_type.is_(None), PromoCode.plan_type == plan.type),
            or_(
                PromoCode.audience == AUDIENCE_EVERYONE,
                PromoCode.allowed_users.any(PromoCodeUser.user_id == user.id),
            ),
        )
    )).scalars().all()

    # Usage limits need a per-code count, so they can't be folded into the query
    # above. The candidate set is tiny (live codes only), so a loop is fine.
    for promo in candidates:
        if promo.max_uses is not None:
            if await count_uses(db, promo.id, exclude_pending_user_id=user.id) >= promo.max_uses:
                continue
        if await count_uses(db, promo.id, user_id=user.id,
                            exclude_pending_user_id=user.id) >= promo.max_uses_per_user:
            continue
        _, final_amount = compute_amounts(plan.amount, promo.discount_percent)
        if final_amount < MIN_CHARGE_BANI:
            continue
        return True
    return False
