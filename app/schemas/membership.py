from datetime import datetime
from typing import Optional
from pydantic import BaseModel, computed_field

class MembershipResponse(BaseModel):
    id: int
    plan: str
    plan_name: str | None = None
    status: str
    amount: int
    start_date: datetime
    end_date: datetime
    freeze_start: Optional[datetime] = None
    freeze_end: Optional[datetime] = None
    created_at: datetime
    # Days still freezable right now — zero once either budget is spent.
    # Set explicitly by the routers via available_freeze_days().
    max_freeze_days: Optional[int] = None
    # The plan's original entitlement and what has been spent, for "x of y"
    # display. Note freeze_days_used already includes an in-progress freeze.
    freeze_days_allowance: Optional[int] = None
    freeze_days_used: int = 0
    freezes_allowance: Optional[int] = None
    freezes_used: int = 0

    @computed_field
    @property
    def freezes_remaining(self) -> Optional[int]:
        if self.freezes_allowance is None:
            return None
        return max(0, self.freezes_allowance - self.freezes_used)

    @computed_field
    @property
    def is_frozen(self) -> bool:
        if self.freeze_start is None or self.freeze_end is None:
            return False
        return self.freeze_start <= datetime.utcnow() <= self.freeze_end

    model_config = {"from_attributes": True}
