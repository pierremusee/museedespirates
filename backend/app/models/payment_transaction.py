import enum
import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, ForeignKey, Numeric, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.reservation import Reservation


class PaymentMethod(enum.StrEnum):
    """Moyens de paiement (DFC n°7).

    - cb    : carte bancaire — seul moyen du canal web, aussi au guichet
    - cash  : espèces — rendu de monnaie possible
    - ancv  : chèques-vacances — aucun rendu, nominal enregistré
    - check : chèque bancaire — réservé aux groupes et scolaires
    """

    CB = "cb"
    CASH = "cash"
    ANCV = "ancv"
    CHECK = "check"


class PaymentStatus(enum.StrEnum):
    COMPLETED = "completed"
    # Valeurs futures envisagées : refunded, cancelled.


class PaymentTransaction(Base):
    """Une tranche d'encaissement rattachée à une réservation (DFC n°7).

    `amount` est le nominal remis par le client (ex. chèque-vacances de
    60 €) ; `applied_amount` est la part réellement imputée au solde.
    Les deux diffèrent pour les ANCV excédentaires — indispensable pour
    le rapprochement de caisse.
    """

    __tablename__ = "payment_transactions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    reservation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("reservations.id"), nullable=False, index=True
    )
    method: Mapped[PaymentMethod] = mapped_column(
        Enum(
            PaymentMethod,
            name="payment_method",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    applied_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    status: Mapped[PaymentStatus] = mapped_column(
        Enum(
            PaymentStatus,
            name="payment_status",
            values_callable=lambda e: [m.value for m in e],
        ),
        default=PaymentStatus.COMPLETED,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    reservation: Mapped["Reservation"] = relationship(back_populates="payments")
