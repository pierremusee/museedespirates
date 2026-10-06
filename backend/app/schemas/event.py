import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.event import EventType


class SessionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    start_time: datetime
    max_capacity: int
    booked_seats: int
    remaining_capacity: int


class EventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    event_type: EventType
    is_active: bool
    sessions: list[SessionRead]
