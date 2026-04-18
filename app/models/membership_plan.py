from sqlalchemy import Boolean, Enum, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

MembershipPlanType = Enum("full_time", "day_time", name="membershipplan_type")


class MembershipPlan(Base):
    __tablename__ = "membership_plans"
    __table_args__ = (UniqueConstraint("key", "type", name="uq_membership_plan_key_type"),)

    id:            Mapped[int]  = mapped_column(Integer, primary_key=True, index=True)
    key:           Mapped[str]  = mapped_column(String(50), nullable=False)
    type:          Mapped[str]  = mapped_column(MembershipPlanType, nullable=False, default="full_time")
    name:          Mapped[str]  = mapped_column(String(100), nullable=False)
    amount:        Mapped[int]  = mapped_column(Integer, nullable=False)   # in bani
    duration_days: Mapped[int]  = mapped_column(Integer, nullable=False)
    is_active:     Mapped[bool] = mapped_column(Boolean, default=True)
