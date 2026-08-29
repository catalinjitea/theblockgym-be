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
        last_day = monthrange(year, month)[1]
        if start.day > last_day:
            # The target month has no day with this number, so the clamped
            # date is already the last one the member is owed. Returning it at
            # end of day instead of falling through to the -1s below is what
            # keeps an Aug 31 start from ending on Sep 29 rather than Sep 30.
            return start.replace(year=year, month=month, day=last_day,
                                 hour=23, minute=59, second=59)
        end = start.replace(year=year, month=month, day=start.day)
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


def is_frozen_at(membership: Membership, moment: datetime) -> bool:
    """True when the membership is paused over `moment`.

    A freeze suspends everything the membership entitles: gym entry while it
    runs, and any class taking place inside the window.
    """
    return (membership.freeze_start is not None
            and membership.freeze_end is not None
            and membership.freeze_start <= moment <= membership.freeze_end)


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
    """Days a freeze is charged for, counted inclusively.

    freeze_end is stored at 23:59:59, so a Jan 1 -> Jan 2 freeze blocks access
    for both days and costs 2. A single-day freeze (start == end) costs 1,
    which is what makes a leftover 1-day balance spendable.
    """
    return (freeze_end - freeze_start).days + 1


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

    # now.date(), not date.today(): the rest of this module works in UTC, and
    # a server on local time would disagree with it either side of midnight.
    if freeze_start < now.date():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Data de început nu poate fi în trecut.")

    if freeze_end < freeze_start:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Data de sfârșit nu poate fi înainte de data de început.")

    # A freeze scheduled past the membership's own expiry would extend end_date
    # for a pause the member can never actually take.
    if datetime.combine(freeze_start, time.min) > membership.end_date:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Înghețarea nu poate începe după expirarea abonamentului.")

    if not freezes_remaining(membership):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Ai folosit toate înghețările disponibile pentru acest abonament.")

    remaining_days = max(0, membership.freeze_days_allowance - membership.freeze_days_used)
    if remaining_days == 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Ai folosit toate zilele de îngheț disponibile pentru acest abonament.")

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

    A freeze is only "taken" once a whole day of it has elapsed. Cancelling
    before the start date, or on the start date itself, undoes it completely —
    days and the freeze both come back, and the member can change their mind
    without penalty.

    Cancelling later refunds the unspent days but keeps the freeze counted.
    That asymmetry is what stops the cycle the caps exist to prevent: letting a
    freeze run most of its length, cancelling, and re-freezing for a fresh
    allowance. A same-day cancel can't be abused the same way, because it hands
    back the whole end_date extension it granted.
    """
    now = datetime.utcnow()

    if (membership.freeze_start is None
            or membership.freeze_end is None
            or membership.freeze_end <= now):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Abonamentul nu este înghețat.")

    charged = freeze_day_count(membership.freeze_start.date(), membership.freeze_end.date())
    elapsed_days = (now.date() - membership.freeze_start.date()).days
    undo = elapsed_days <= 0

    if undo:
        spent = 0
    else:
        # The day it is cancelled on counts as spent — the member was frozen
        # for part of it.
        spent = min(freeze_day_count(membership.freeze_start.date(), now.date()), charged)

    # Never refund more than was actually charged. Guards freezes booked before
    # the inclusive-counting change, whose stored window is a day longer than
    # what came off the allowance.
    refund = min(charged - spent, membership.freeze_days_used)
    membership.end_date -= timedelta(days=refund)
    membership.freeze_days_used = max(0, membership.freeze_days_used - refund)

    if undo:
        # Never really happened: give the freeze back and leave no trace.
        membership.freezes_used = max(0, membership.freezes_used - 1)
        membership.freeze_start = None
        membership.freeze_end = None
    else:
        # Close the window now; the days already spent stay charged.
        membership.freeze_end = now

    return refund
