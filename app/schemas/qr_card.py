from datetime import date, datetime
from typing import Optional
from pydantic import BaseModel

class QRCardResponse(BaseModel):
    id: int
    code: str
    type: str
    is_active: bool
    membership_id: Optional[int]
    created_at: datetime

    model_config = {"from_attributes": True}

class GenerateQRCardsRequest(BaseModel):
    count: int = 10

class ActivateQRCardRequest(BaseModel):
    user_id: int
    plan_key: str
    plan_type: str
    start_date: date
