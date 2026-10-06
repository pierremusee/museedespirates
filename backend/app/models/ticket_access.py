import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import Date, DateTime, Enum, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.ticket import TicketType

if TYPE_CHECKING:
    from app.models.event import Event
    from app.models.session import Session
    from app.models.ticket import Ticket


class TicketAccess(Base):
    """Droit d'accès unitaire porté par un billet — consommé au scan.

    - `open_ticket` : `session_id` nul, validité portée par `valid_date`.
    - `session_*`   : `session_id` obligatoire, jauge strictement contrôlée.
    `is_scanned`/`scanned_at` vivent ici : chaque droit est à usage
    unique, indépendamment des autres accès du même billet.
    """

    __tablename__ = "ticket_accesses"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    ticket_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tickets.id"), nullable=False, index=True
    )
    access_type: Mapped[TicketType] = mapped_column(
        Enum(
            TicketType,
            name="ticket_type",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("sessions.id"), nullable=True, index=True
    )
    event_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("events.id"), nullable=True, index=True
    )
    valid_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_scanned: Mapped[bool] = mapped_column(default=False, nullable=False)
    scanned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    ticket: Mapped["Ticket"] = relationship(back_populates="accesses")
    session: Mapped["Session | None"] = relationship(back_populates="accesses")
    event: Mapped["Event | None"] = relationship()

    @property
    def session_start(self) -> datetime | None:
        # __dict__ évite de déclencher un lazy load (interdit en async)
        # si la séance n'a pas été chargée en eager.
        session = self.__dict__.get("session")
        return session.start_time if session else None
