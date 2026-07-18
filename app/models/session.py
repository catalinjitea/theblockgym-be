from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class Session(Base):
    __tablename__ = "sessions"

    id:               Mapped[int]      = mapped_column(Integer, primary_key=True, index=True)
    trainer_id:       Mapped[int]      = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    title:            Mapped[str]      = mapped_column(String(200), nullable=False)
    start_datetime:   Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    duration_minutes: Mapped[int]      = mapped_column(Integer, nullable=False)
    max_capacity:     Mapped[int]      = mapped_column(Integer, nullable=False)

    trainer  = relationship("User", back_populates="sessions")
    bookings = relationship("Booking", back_populates="session")
