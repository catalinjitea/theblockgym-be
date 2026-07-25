from calendar import monthrange
from datetime import date, datetime, time, timedelta
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.membership import Membership
from app.models.membership_plan import MembershipPlan


def compute_end_date(start: datetime, plan: MembershipPlan) -> datetime:
    """Return the membership end date given a start date and plan.

    Uses month-based arithmetic when duration_months is set so that
    e.g. a plan starting on Jan 31 ends on Feb 28/29, not Mar 2/3.
    Falls back to duration_days for day-based plans.
    """
    if plan.duration_months:
        month = start.month - 1 + plan.duration_months
        year = start.year + month // 12
        month = month % 12 + 1
        day = min(start.day, monthrange(year, month)[1])
        end = start.replace(year=year, month=month, day=day)
    else:
        end = start + timedelta(days=plan.duration_days)
    return end - timedelta(seconds=1)


# ── Freeze entitlement ────────────────────────────────────────────────────────

def snapshot_freeze_allowance(plan: MembershipPlan) -> dict[str, Optional[int]]:
    """Freeze entitlement to copy onto a new membership.

    Splat into the Membership constructor at every creation site. Copying
    rather than looking up keeps the terms fixed at what was sold: editing or
    retiring a plan afterwards can no longer change an existing membership,
    and a plan row can be resolved exactly here (by key *and* type), which is
    impossible later because memberships only record the key.
    """
    return {
        "freeze_days_allowance": plan.max_freeze_days,
        "freezes_allowance": plan.max_freezes,
    }


def available_freeze_days(membership: Membership) -> Optional[int]:
    """Days this membership may freeze for right now.

    None when the plan never permitted freezing. Zero when either budget is
    spent — the day allowance or the number of separate freezes. Clients use
    this single number to decide whether to offer the freeze action at all.
    """
    if membership.freeze_days_allowance is None:
        return None
    if (membership.freezes_allowance is not None
            and membership.freezes_used >= membership.freezes_allowance):
        return 0
    return max(0, membership.freeze_days_allowance - membership.freeze_days_used)


def freezes_remaining(membership: Membership) -> Optional[int]:
    if membership.freezes_allowance is None:
        return None
    return max(0, membership.freezes_allowance - membership.freezes_used)


def freeze_day_count(freeze_start: date, freeze_end: date) -> int:
    """Days a freeze is charged for.

    Exclusive: Jan 1 -> Jan 2 costs 1. This under-counts by a day, since
    freeze_end is stored at 23:59:59 and access is actually blocked for both
    days, but it matches what end_date is extended by and what the frontend
    displays. Correcting it means changing both sides at once — deliberately
    left alone here so this change stays backend-only.
    """
    return (freeze_end - freeze_start).days


# ── Locking loaders ───────────────────────────────────────────────────────────
# Freeze and unfreeze read a membership, decide against its counters, then
# write them back. Without a row lock two concurrent requests can both pass
# the checks and both spend the same allowance.

async def load_active_membership_for_update(
    db: AsyncSession, user_id: int
) -> Optional[Membership]:
    result = await db.execute(
        select(Membership)
        .where(Membership.user_id == user_id, Membership.status == "activ")
        .order_by(Membership.created_at.desc())
        .limit(1)
        .with_for_update()
    )
    return result.scalars().first()


async def load_membership_for_update(
    db: AsyncSession, membership_id: int
) -> Optional[Membership]:
    result = await db.execute(
        select(Membership).where(Membership.id == membership_id).with_for_update()
    )
    return result.scalar_one_or_none()


# ── Freeze / unfreeze ─────────────────────────────────────────────────────────

def apply_freeze(membership: Membership, freeze_start: date, freeze_end: date) -> int:
    """Validate and apply a freeze, returning the days charged.

    Raises HTTPException on any rule violation. Shared by the member and admin
    endpoints so the two can't drift apart.
    """
    now = datetime.utcnow()

    if membership.start_date > now or membership.end_date < now:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Nu există un abonament activ.")

    if (membership.freeze_start is not None
            and membership.freeze_end is not None
            and membership.freeze_end > now):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Abonamentul este deja înghețat.")

    if membership.freeze_days_allowance is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Planul nu permite înghețarea abonamentului.")

    if freeze_start < date.today():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Data de început nu poate fi în trecut.")

    if freeze_end <= freeze_start:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Data de sfârșit trebuie să fie după data de început.")

    # A freeze scheduled past the membership's own expiry would extend end_date
    # for a pause the member can never actually take.
    if datetime.combine(freeze_start, time.min) > membership.end_date:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Înghețarea nu poate începe după expirarea abonamentului.")

    if not freezes_remaining(membership):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Ai folosit toate înghețările disponibile pentru acest abonament.")

    remaining_days = max(0, membership.freeze_days_allowance - membership.freeze_days_used)
    days = freeze_day_count(freeze_start, freeze_end)
    if days > remaining_days:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Perioada de îngheț nu poate depăși {remaining_days} zile.",
        )

    membership.freeze_start = datetime.combine(freeze_start, time.min)
    membership.freeze_end = datetime.combine(freeze_end, time(23, 59, 59))
    membership.end_date += timedelta(days=days)
    membership.freeze_days_used += days
    membership.freezes_used += 1
    return days


def cancel_freeze(membership: Membership) -> int:
    """Cancel the active or scheduled freeze, returning the days refunded.

    A freeze that never started is undone completely, including the freeze
    itself. One already under way refunds only its unspent days and still
    counts against the freeze allowance — otherwise freeze/unfreeze could be
    cycled indefinitely at no cost.
    """
    now = datetime.utcnow()

    if (membership.freeze_start is None
            or membership.freeze_end is None
            or membership.freeze_end <= now):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Abonamentul nu este înghețat.")

    charged = freeze_day_count(membership.freeze_start.date(), membership.freeze_end.date())
    started = membership.freeze_start <= now

    if started:
        spent = (now.date() - membership.freeze_start.date()).days
        spent = min(max(spent, 0), charged)
    else:
        spent = 0

    refund = charged - spent
    membership.end_date -= timedelta(days=refund)
    membership.freeze_days_used = max(0, membership.freeze_days_used - refund)

    if started:
        membership.freeze_end = now
    else:
        # Never happened: leave no trace of a scheduled freeze.
        membership.freezes_used = max(0, membership.freezes_used - 1)
        membership.freeze_start = None
        membership.freeze_end = None

    return refund
