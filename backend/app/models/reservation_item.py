import enum
import uuid
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import Date, Enum, ForeignKey, Integer, Numeric
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.ticket import TicketCategory

if TYPE_CHECKING:
    from app.models.product import Product
    from app.models.reservation import Reservation
    from app.models.ticket import Ticket


class FreeProfile(enum.StrEnum):
    """Profil de gratuité (DFC n°6) — ticket émis à 0 €, jamais sans billet."""

    UNDER_4 = "under_4"            # < 4 ans -> ticket enfant
    DISABILITY = "disability"      # carte d'invalidité -> catégorie de l'item
    PMR_COMPANION = "pmr_companion"  # accompagnateur PMR -> ticket adulte


class ReservationItem(Base):
    """Ligne de commande : un produit acheté dans une réservation.

    Chaque item émet un ou plusieurs tickets (1+ par personne selon les
    composants du produit). `computed_price` et `season_modifier` figent
    le prix appliqué au moment de l'achat (traçabilité comptable).
    """

    __tablename__ = "reservation_items"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    reservation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("reservations.id"), nullable=False, index=True
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id"), nullable=False, index=True
    )
    category: Mapped[TicketCategory | None] = mapped_column(
        Enum(
            TicketCategory,
            name="ticket_category",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=True,
    )
    extra_children: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    free_profile: Mapped[FreeProfile | None] = mapped_column(
        Enum(
            FreeProfile,
            name="free_profile",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=True,
    )
    # Paramètres d'émission figés à la commande : les tickets ne sont
    # émis qu'à la confirmation du paiement (DFC n°7), il faut donc
    # conserver la date de visite et les séances choisies.
    visit_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    session_ids: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # Taille du groupe pour les produits `kind = group` (minimum 8).
    group_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    computed_price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    season_modifier: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), default=0, nullable=False
    )

    reservation: Mapped["Reservation"] = relationship(back_populates="items")
    product: Mapped["Product"] = relationship(back_populates="items")
    tickets: Mapped[list["Ticket"]] = relationship(
        back_populates="item", cascade="all, delete-orphan"
    )

    @property
    def product_code(self) -> str:
        return self.product.code
