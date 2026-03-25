from datetime import datetime
from pydantic import BaseModel

class MembershipResponse(BaseModel):
    id: int
    plan: str
    status: str
    amount: int
    start_date: datetime
    end_date: datetime
    created_at: datetime

    model_config = {"from_attributes": True}
