from datetime import datetime
from typing import Optional
from pydantic import BaseModel, computed_field

class MembershipResponse(BaseModel):
    id: int
    plan: str
    plan_name: str | None = None
    sessions_total: int | None = None
    sessions_remaining: int | None = None
    status: str
    amount: int
    start_date: datetime
    end_date: datetime
    freeze_start: Optional[datetime] = None
    freeze_end: Optional[datetime] = None
    created_at: datetime
    max_freeze_days: Optional[int] = None

    @computed_field
    @property
    def is_frozen(self) -> bool:
        if self.freeze_start is None or self.freeze_end is None:
            return False
        return self.freeze_start <= datetime.utcnow() <= self.freeze_end

    model_config = {"from_attributes": True}
