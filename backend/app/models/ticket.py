import enum
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Enum, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.reservation import Reservation
    from app.models.reservation_item import ReservationItem
    from app.models.ticket_access import TicketAccess


class TicketCategory(str, enum.Enum):
    ADULT = "adult"
    CHILD = "child"
    REDUCED = "reduced"
    GROUP = "group"
    SCHOOL = "school"


class TicketType(str, enum.Enum):
    """Type d'accès (DFC n°2/n°3) : pilote la règle de validation au scan."""

    OPEN_TICKET = "open_ticket"          # Musée : valide à la journée civile
    SESSION_STANDARD = "session_standard"  # Théâtre : séance stricte
    SESSION_DINING = "session_dining"      # Dîner-spectacle : séance + repas


class MenuChoice(str, enum.Enum):
    VIANDE = "viande"
    POISSON = "poisson"
    VEGETARIEN = "vegetarien"
    ENFANT = "enfant"


class Ticket(Base):
    """Billet par personne — `id` sert de jeton unique encodé dans le QR code.

    Un billet porte un ou plusieurs `TicketAccess` (entrée musée,
    séance(s) de théâtre…) consommés indépendamment : le même QR est
    présenté à chaque poste de contrôle. Les droits sont fusionnés sur
    toute la réservation à l'émission (personnes de même catégorie et
    profil de gratuité).
    """

    __tablename__ = "tickets"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    reservation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("reservations.id"), nullable=False, index=True
    )
    item_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("reservation_items.id"), nullable=True, index=True
    )
    ticket_category: Mapped[TicketCategory] = mapped_column(
        Enum(
            TicketCategory,
            name="ticket_category",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )
    menu_choice: Mapped[MenuChoice | None] = mapped_column(
        Enum(
            MenuChoice,
            name="menu_choice",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=True,
    )

    reservation: Mapped["Reservation"] = relationship(back_populates="tickets")
    item: Mapped["ReservationItem | None"] = relationship(back_populates="tickets")
    accesses: Mapped[list["TicketAccess"]] = relationship(
        back_populates="ticket", cascade="all, delete-orphan"
    )
