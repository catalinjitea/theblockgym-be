"""Scheduled push notifications for the mobile app.

  * Class reminder: about an hour before each confirmed booking.
  * Expiry alerts: 3 days before a membership's last day, and on that day,
    unless the member already has a later membership (renewed in advance).

Both respect the member's toggles in user_preferences (on by default) and
are sent in the member's saved language. `notification_loop` runs them every
few minutes inside the web process; set NOTIFICATION_JOBS_ENABLED=false to
turn it off (e.g. on a second replica, or locally).
"""
import asyncio
import logging
import os
from datetime import datetime, timedelta

from sqlalchemy import and_, exists, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.core.push import PushMessage, send_push
from app.core.timeutils import ro_now
from app.models.booking import Booking
from app.models.membership import Membership
from app.models.notification_log import NotificationLog
from app.models.push_device import PushDevice
from app.models.session import Session
from app.models.user_preferences import UserPreferences

logger = logging.getLogger(__name__)

JOBS_ENABLED = os.getenv("NOTIFICATION_JOBS_ENABLED", "true").lower() != "false"
INTERVAL_SECONDS = int(os.getenv("NOTIFICATION_JOBS_INTERVAL_SECONDS", "300"))
CLASS_REMINDER_LEAD = timedelta(hours=1)
# Expiry alerts only go out during the day, Romanian time
EXPIRY_ALERT_HOURS = range(10, 21)

MONTHS_RO = ["ianuarie", "februarie", "martie", "aprilie", "mai", "iunie", "iulie",
             "august", "septembrie", "octombrie", "noiembrie", "decembrie"]

COPY = {
    "class_reminder": {
        "ro": ("Clasa ta începe în curând", "{title} începe la {time}. Ne vedem la sală!"),
        "en": ("Your class starts soon", "{title} starts at {time}. See you at the gym!"),
    },
    "expiry_3d": {
        "ro": ("Abonamentul expiră în curând", "Abonamentul tău mai este valabil 3 zile, până pe {date}. Îl poți reînnoi din secțiunea Abonament."),
        "en": ("Your membership ends soon", "Your membership is valid for 3 more days, until {date}. You can renew it from the Membership tab."),
    },
    "expiry_last_day": {
        "ro": ("Ultima zi de abonament", "Azi este ultima zi a abonamentului tău. Îl poți reînnoi din secțiunea Abonament."),
        "en": ("Last day of your membership", "Today is the last day of your membership. You can renew it from the Membership tab."),
    },
}


def _format_day(day, language: str) -> str:
    if language == "en":
        return f"{day.day} {day.strftime('%B')}"
    return f"{day.day} {MONTHS_RO[day.month - 1]}"


def _last_day(membership: Membership):
    # End dates are usually "day X 23:59:59", some older ones "day X+1 00:00";
    # one second earlier lands on the member's last day of access in both cases
    return (membership.end_date - timedelta(seconds=1)).date()


async def _claim(db: AsyncSession, user_id: int, kind: str, ref_id: int) -> bool:
    """Record the notification; False if it was already sent (or claimed)."""
    result = await db.execute(
        insert(NotificationLog)
        .values(user_id=user_id, kind=kind, ref_id=ref_id, created_at=datetime.utcnow())
        .on_conflict_do_nothing(constraint="uq_notification_log_kind_ref")
        .returning(NotificationLog.id)
    )
    return result.scalar_one_or_none() is not None


async def _preferences(db: AsyncSession, user_ids: set[int]) -> dict[int, UserPreferences]:
    if not user_ids:
        return {}
    rows = await db.execute(select(UserPreferences).where(UserPreferences.user_id.in_(user_ids)))
    return {p.user_id: p for p in rows.scalars()}


async def _devices(db: AsyncSession, user_ids: set[int]) -> dict[int, list[str]]:
    if not user_ids:
        return {}
    rows = await db.execute(select(PushDevice.user_id, PushDevice.expo_push_token).where(PushDevice.user_id.in_(user_ids)))
    devices: dict[int, list[str]] = {}
    for user_id, token in rows.all():
        devices.setdefault(user_id, []).append(token)
    return devices


async def send_class_reminders(db: AsyncSession, now: datetime | None = None) -> int:
    now = now or ro_now()
    rows = await db.execute(
        select(Booking.id, Booking.user_id, Session.title, Session.start_datetime)
        .join(Session, Booking.session_id == Session.id)
        .where(
            Booking.status == "confirmed",
            Session.start_datetime > now,
            Session.start_datetime <= now + CLASS_REMINDER_LEAD,
        )
    )
    due = rows.all()
    user_ids = {r.user_id for r in due}
    prefs, devices = await _preferences(db, user_ids), await _devices(db, user_ids)

    messages: list[PushMessage] = []
    for booking_id, user_id, title, start in due:
        p = prefs.get(user_id)
        if (p and not p.class_reminders) or not devices.get(user_id):
            continue
        if not await _claim(db, user_id, "class_reminder", booking_id):
            continue
        language = p.language if p else "ro"
        heading, body = COPY["class_reminder"][language]
        text = body.format(title=title, time=start.strftime("%H:%M"))
        messages += [PushMessage(to=token, title=heading, body=text, data={"url": "/clase"}) for token in devices[user_id]]
    return await send_push(db, messages)


async def send_expiry_alerts(db: AsyncSession, now: datetime | None = None) -> int:
    now = now or ro_now()
    today = now.date()
    later = Membership.__table__.alias("later")
    rows = await db.execute(
        select(Membership).where(
            Membership.status == "activ",
            Membership.end_date > now,
            Membership.end_date <= datetime.combine(today + timedelta(days=4), datetime.min.time()) + timedelta(seconds=1),
            # Skip members who already bought what comes next
            ~exists().where(
                and_(
                    later.c.user_id == Membership.user_id,
                    later.c.status == "activ",
                    later.c.end_date > Membership.end_date,
                )
            ),
        )
    )
    candidates = rows.scalars().all()
    user_ids = {m.user_id for m in candidates}
    prefs, devices = await _preferences(db, user_ids), await _devices(db, user_ids)

    messages: list[PushMessage] = []
    for m in candidates:
        days_before = (_last_day(m) - today).days
        kind = {3: "expiry_3d", 0: "expiry_last_day"}.get(days_before)
        p = prefs.get(m.user_id)
        if kind is None or (p and not p.expiry_alerts) or not devices.get(m.user_id):
            continue
        if not await _claim(db, m.user_id, kind, m.id):
            continue
        language = p.language if p else "ro"
        heading, body = COPY[kind][language]
        text = body.format(date=_format_day(_last_day(m), language))
        messages += [PushMessage(to=token, title=heading, body=text, data={"url": "/abonament"}) for token in devices[m.user_id]]
    return await send_push(db, messages)


async def run_notification_jobs() -> None:
    async with AsyncSessionLocal() as db:
        sent = await send_class_reminders(db)
        if ro_now().hour in EXPIRY_ALERT_HOURS:
            sent += await send_expiry_alerts(db)
        await db.commit()
    if sent:
        logger.info("Sent %d push notifications", sent)


async def notification_loop() -> None:
    while True:
        try:
            await run_notification_jobs()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Notification jobs failed")
        await asyncio.sleep(INTERVAL_SECONDS)
