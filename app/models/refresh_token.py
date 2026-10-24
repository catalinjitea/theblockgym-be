from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class RefreshToken(Base):
    """A mobile app refresh token, stored as its SHA-256 hash.

    Tokens rotate on every use: refreshing marks the row used and issues a new
    row in the same family. Presenting a token that was already used means it
    leaked (or a client bug), so the whole family is revoked and the device
    must log in again. Logout revokes the family too.

    Times here are UTC (datetime.utcnow), unlike membership and session dates,
    which are naive Romanian local time. They are only ever compared to each other.
    """

    __tablename__ = "refresh_tokens"

    id:         Mapped[int]                = mapped_column(Integer, primary_key=True, index=True)
    user_id:    Mapped[int]                = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    # One family per login; every rotation stays in it
    family_id:  Mapped[str]                = mapped_column(String(36), nullable=False, index=True)
    token_hash: Mapped[str]                = mapped_column(String(64), nullable=False, unique=True, index=True)
    expires_at: Mapped[datetime]           = mapped_column(DateTime, nullable=False)
    used_at:    Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime]           = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
