from datetime import datetime
from pydantic import BaseModel


class BookedUserResponse(BaseModel):
    first_name: str
    last_name: str


class TrainerSessionResponse(BaseModel):
    id: int
    title: str
    start_datetime: datetime
    duration_minutes: int
    max_capacity: int
    booked_count: int
    bookings: list[BookedUserResponse]


class SessionResponse(BaseModel):
    id: int
    title: str
    trainer_name: str
    start_datetime: datetime
    duration_minutes: int
    max_capacity: int
    booked_count: int
    is_booked: bool = False

    model_config = {"from_attributes": True}


class BookingResponse(BaseModel):
    id: int
    session_id: int
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


class MySessionResponse(BaseModel):
    booking_id: int
    session_id: int
    title: str
    trainer_name: str
    start_datetime: datetime
    duration_minutes: int
    status: str
