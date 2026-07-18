from datetime import datetime
from zoneinfo import ZoneInfo

ROMANIA_TZ = ZoneInfo("Europe/Bucharest")


def ro_now() -> datetime:
    """Current time as naive Romanian wall-clock.

    Session and membership datetimes are stored naive in Romanian local
    time, so comparisons must use this instead of datetime.now()
    (server-local) or datetime.utcnow().
    """
    return datetime.now(ROMANIA_TZ).replace(tzinfo=None)
