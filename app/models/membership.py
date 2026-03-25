from datetime import datetime
from typing import Optional
from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

class Membership(Base):
    __tablename__ = "memberships"

    id:         Mapped[int]               = mapped_column(Integer, primary_key=True, index=True)
    user_id:    Mapped[int]               = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    plan:       Mapped[str]               = mapped_column(String(50), nullable=False)  # "lunar" | "3luni" | "6luni" | "anual"
    status:     Mapped[str]               = mapped_column(String(20), default="activ")  # "activ" | "expirat" | "anulat"
    amount:     Mapped[int]               = mapped_column(Integer, nullable=False)       # in bani (RON × 100)
    start_date: Mapped[datetime]          = mapped_column(DateTime, nullable=False)
    end_date:   Mapped[datetime]          = mapped_column(DateTime, nullable=False)
    stripe_session_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime]          = mapped_column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="memberships")
