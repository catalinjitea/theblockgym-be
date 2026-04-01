from datetime import datetime
from typing import Optional
from pydantic import BaseModel

class QRCardResponse(BaseModel):
    id: int
    code: str
    is_active: bool
    membership_id: Optional[int]
    created_at: datetime

    model_config = {"from_attributes": True}

class GenerateQRCardsRequest(BaseModel):
    count: int = 10  # number of cards to generate

class ActivateQRCardRequest(BaseModel):
    membership_id: int
