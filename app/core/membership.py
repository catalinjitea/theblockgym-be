from calendar import monthrange
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import func, select
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


async def get_plan_freeze_days_map(db: AsyncSession) -> dict[str, int]:
    """Map each plan key to its max_freeze_days.

    Matched by key alone, ignoring is_active and amount: a retired or
    repriced plan still governs freeze eligibility for the memberships
    that were bought under it. If a key's full_time/day_time rows ever
    disagree, the larger value wins.
    """
    rows = (await db.execute(
        select(MembershipPlan.key, func.max(MembershipPlan.max_freeze_days))
        .group_by(MembershipPlan.key)
    )).all()
    return {key: days for key, days in rows if days is not None}


async def resolve_freeze_days(db: AsyncSession, membership: Membership) -> Optional[int]:
    """Return how many days `membership` may still be frozen for.

    Currently resolved from the plan's max_freeze_days. Once memberships
    track their own remaining freeze allowance (e.g. an
    `available_freeze_days` column), this is the one place to switch over.
    """
    freeze_days_by_key = await get_plan_freeze_days_map(db)
    return freeze_days_by_key.get(membership.plan)
