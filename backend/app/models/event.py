import enum
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Enum, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.session import Session


class EventType(str, enum.Enum):
    PERMANENT_EXHIBITION = "permanent_exhibition"
    THEATER = "theater"
    GUIDED_TOUR = "guided_tour"


class Event(Base):
    """Entité du Complexe : porte le calendrier (via ses séances) et la
    jauge. La tarification relève du catalogue `products`, pas d'ici."""

    __tablename__ = "events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    event_type: Mapped[EventType] = mapped_column(
        Enum(EventType, name="event_type", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)

    sessions: Mapped[list["Session"]] = relationship(
        back_populates="event",
        cascade="all, delete-orphan",
        order_by="Session.start_time",
    )
