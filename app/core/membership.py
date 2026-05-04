from calendar import monthrange
from datetime import datetime, timedelta

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
