import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.event import Event
    from app.models.ticket_access import TicketAccess

# Tolérance après le début de séance : fenêtre commune à la vente
# (reservation_service), au contrôle d'accès (ticket_service) et à
# l'API (is_expired exposé via SessionRead).
LATE_TOLERANCE = timedelta(minutes=30)


class Session(Base):
    """Séance d'un événement — moteur anti-surbooking.

    `booked_seats` est incrémenté sous verrou transactionnel
    (SELECT ... FOR UPDATE) lors des réservations.
    """

    __tablename__ = "sessions"
    __table_args__ = (
        CheckConstraint(
            "booked_seats <= max_capacity", name="check_seats_capacity"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("events.id"), nullable=False, index=True
    )
    start_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    max_capacity: Mapped[int] = mapped_column(Integer, nullable=False)
    booked_seats: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    event: Mapped["Event"] = relationship(back_populates="sessions")
    accesses: Mapped[list["TicketAccess"]] = relationship(back_populates="session")

    @property
    def remaining_capacity(self) -> int:
        return self.max_capacity - self.booked_seats

    @property
    def is_expired(self) -> bool:
        """Début + tolérance dépassé : ni vendable ni scannable."""
        return (
            datetime.now(UTC) > self.start_time + LATE_TOLERANCE
        )
