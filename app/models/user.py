from datetime import datetime
from typing import List, Optional
from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

class User(Base):
    __tablename__ = "users"

    id:                            Mapped[int]                = mapped_column(Integer, primary_key=True, index=True)
    first_name:                    Mapped[str]                = mapped_column(String(100), nullable=False)
    last_name:                     Mapped[str]                = mapped_column(String(100), nullable=False)
    email:                         Mapped[str]                = mapped_column(String(255), unique=True, index=True, nullable=False)
    phone_number:                  Mapped[str]                = mapped_column(String(20), nullable=False)
    age:                           Mapped[Optional[int]]      = mapped_column(Integer, nullable=True)
    hashed_password:               Mapped[str]                = mapped_column(String(255), nullable=False)
    is_active:                     Mapped[bool]               = mapped_column(Boolean, default=True)
    is_admin:                      Mapped[bool]               = mapped_column(Boolean, default=False)
    terms_accepted_at:             Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, default=None)
    privacy_accepted_at:           Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, default=None)
    created_at:                    Mapped[datetime]           = mapped_column(DateTime, default=datetime.utcnow)
    password_reset_token:          Mapped[Optional[str]]      = mapped_column(String(255), nullable=True, default=None)
    password_reset_token_expires:  Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, default=None)

    memberships: Mapped[List["Membership"]] = relationship("Membership", back_populates="user", order_by="Membership.created_at.desc()")
