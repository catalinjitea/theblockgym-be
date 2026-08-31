from datetime import datetime
from typing import List, Optional

from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

AUDIENCE_NAMED = "named"
AUDIENCE_EVERYONE = "everyone"
AUDIENCES = (AUDIENCE_NAMED, AUDIENCE_EVERYONE)


class PromoCode(Base):
    """A percentage-off code redeemable at online checkout.

    One row covers every campaign shape we need:
      * unique per-person codes  → many rows, each max_uses=1
      * one shared code, select people → audience="named" + promo_code_users
      * open campaign code       → audience="everyone", max_uses cap

    `audience` states who the code is for, rather than leaving it to be inferred
    from whether promo_code_users happens to have rows. An empty allowlist and a
    deliberately public code are different intentions and must not look alike —
    reading them as the same thing once put the discount field in front of every
    member of the gym.
    """

    __tablename__ = "promo_codes"

    id:               Mapped[int]  = mapped_column(Integer, primary_key=True, index=True)
    code:             Mapped[str]  = mapped_column(String(50), unique=True, index=True, nullable=False)
    discount_percent: Mapped[int]  = mapped_column(Integer, nullable=False)

    # AUDIENCE_NAMED    — only members listed in promo_code_users
    # AUDIENCE_EVERYONE — any logged-in member; the allowlist is left empty
    audience: Mapped[str] = mapped_column(String(20), nullable=False,
                                          default="named", server_default="named")

    # null = unlimited
    max_uses:          Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    max_uses_per_user: Mapped[int]           = mapped_column(Integer, nullable=False, default=1, server_default="1")

    valid_from:  Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    valid_until: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # null = applies to every plan / every plan type
    plan_key:  Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    plan_type: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)

    is_active:  Mapped[bool]     = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    allowed_users: Mapped[List["PromoCodeUser"]] = relationship(
        "PromoCodeUser", back_populates="promo_code", cascade="all, delete-orphan"
    )
    redemptions: Mapped[List["PromoRedemption"]] = relationship(
        "PromoRedemption", back_populates="promo_code", cascade="all, delete-orphan"
    )


class PromoCodeUser(Base):
    """Allowlist entry. Rows existing for a code restrict it to exactly those users."""

    __tablename__ = "promo_code_users"
    __table_args__ = (UniqueConstraint("promo_code_id", "user_id", name="uq_promo_code_user"),)

    id:            Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    promo_code_id: Mapped[int] = mapped_column(Integer, ForeignKey("promo_codes.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id:       Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    promo_code = relationship("PromoCode", back_populates="allowed_users")
    user       = relationship("User")


class PromoRedemption(Base):
    """Links one checkout order to the code it used.

    Written at checkout as `pending` — the discount is only known there — and
    flipped to `confirmed` by the IPN handler once payment actually lands. That
    keyed-by-order_id lookup is what lets the discount reach the IPN without
    changing the orderID format it parses.

    `Membership.payment_session_id` is the same order_id, so a membership can
    always be joined back to the discount that produced it.
    """

    __tablename__ = "promo_redemptions"

    id:            Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    order_id:      Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    promo_code_id: Mapped[int] = mapped_column(Integer, ForeignKey("promo_codes.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id:       Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    # Snapshotted at checkout so editing or deleting a code later never rewrites history.
    discount_percent: Mapped[int] = mapped_column(Integer, nullable=False)
    original_amount:  Mapped[int] = mapped_column(Integer, nullable=False)   # bani
    discount_amount:  Mapped[int] = mapped_column(Integer, nullable=False)   # bani
    final_amount:     Mapped[int] = mapped_column(Integer, nullable=False)   # bani

    status:       Mapped[str]                = mapped_column(String(20), nullable=False, default="pending", server_default="pending")
    created_at:   Mapped[datetime]           = mapped_column(DateTime, default=datetime.utcnow)
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    promo_code = relationship("PromoCode", back_populates="redemptions")
    user       = relationship("User")
