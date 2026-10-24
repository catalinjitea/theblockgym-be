from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class UserPreferences(Base):
    """A member's app settings, one row per member, created on first save.

    Kept out of `users` on purpose: a new column there makes every user query
    fail if the new code starts before its migration runs, while a new table is
    created by create_all on startup and only affects the preferences endpoints.
    """

    __tablename__ = "user_preferences"

    user_id:    Mapped[int]      = mapped_column(Integer, ForeignKey("users.id"), primary_key=True)
    # "ro" or "en": the app's UI language, and the language of its push notifications
    language:   Mapped[str]      = mapped_column(String(5), nullable=False, default="ro")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
