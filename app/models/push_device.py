from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class PushDevice(Base):
    """A phone registered for push notifications through Expo's push service.

    A token identifies one app install. It belongs to whoever registered it
    last: if another member logs in on the same phone, the row moves to them.
    """

    __tablename__ = "push_devices"

    id:              Mapped[int]           = mapped_column(Integer, primary_key=True, index=True)
    user_id:         Mapped[int]           = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    expo_push_token: Mapped[str]           = mapped_column(String(255), nullable=False, unique=True, index=True)
    platform:        Mapped[str]           = mapped_column(String(10), nullable=False)
    locale:          Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    created_at:      Mapped[datetime]      = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    last_seen_at:    Mapped[datetime]      = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
