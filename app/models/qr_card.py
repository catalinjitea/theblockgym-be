from datetime import datetime
from typing import Optional
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

class QRCard(Base):
    __tablename__ = "qr_cards"

    id:            Mapped[int]               = mapped_column(Integer, primary_key=True, index=True)
    code:          Mapped[str]               = mapped_column(String(100), unique=True, index=True, nullable=False)
    is_active:     Mapped[bool]              = mapped_column(Boolean, default=False)
    membership_id: Mapped[Optional[int]]     = mapped_column(Integer, ForeignKey("memberships.id"), nullable=True)
    created_at:    Mapped[datetime]          = mapped_column(DateTime, default=datetime.utcnow)

    membership = relationship("Membership", back_populates="qr_card")
