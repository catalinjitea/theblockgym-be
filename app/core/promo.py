from datetime import datetime
from typing import Optional

# ── FREE-WEEK-PROMO ───────────────────────────────────────────────────────────
# Free-classes week (Mon 2026-08-10 through Sat 2026-08-16): sessions starting
# inside this window are bookable by anyone whose active membership covers the
# session date — no group_classes plan required — and never consume a
# group-plan session quota.
#
# Everything is keyed on the *session's* date, so the promo switches itself off
# once the week has passed; nothing needs restoring. Cleanup afterwards is
# deletion only — `grep -rn FREE-WEEK-PROMO app/` lists every touchpoint:
#
#   1. The free-week branch in _group_classes_booking_error
#      (app/routers/sessions.py) — safe to delete any time after 2026-08-16.
#   2. The quota filter in count_sessions_used (app/core/membership.py) and
#      this module — KEEP until no active group-classes membership's window
#      overlaps the promo week, or free-week bookings get charged
#      retroactively against paid session packs.
#
# Datetimes are naive Romanian wall-clock, matching how sessions are stored.
# Set both to None to switch the promo off early.
FREE_CLASSES_FROM: Optional[datetime] = datetime(2026, 8, 10)
FREE_CLASSES_UNTIL: Optional[datetime] = datetime(2026, 8, 17)


def is_free_class_session(start_datetime: datetime) -> bool:
    """True when the session takes place inside the free-classes window."""
    if FREE_CLASSES_FROM is None or FREE_CLASSES_UNTIL is None:
        return False
    return FREE_CLASSES_FROM <= start_datetime < FREE_CLASSES_UNTIL
