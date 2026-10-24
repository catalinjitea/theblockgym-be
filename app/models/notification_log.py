from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class NotificationLog(Base):
    """One row per notification a member was sent, so the jobs never repeat one.

    The row is inserted before sending (insert-if-absent), which makes sends
    at-most-once even if two processes run the jobs at the same time.
    `ref_id` is the booking id for class reminders, the membership id for
    expiry alerts.
    """

    __tablename__ = "notification_log"
    __table_args__ = (UniqueConstraint("kind", "ref_id", name="uq_notification_log_kind_ref"),)

    id:         Mapped[int]      = mapped_column(Integer, primary_key=True, index=True)
    user_id:    Mapped[int]      = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    # class_reminder | expiry_3d | expiry_last_day
    kind:       Mapped[str]      = mapped_column(String(30), nullable=False)
    ref_id:     Mapped[int]      = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
