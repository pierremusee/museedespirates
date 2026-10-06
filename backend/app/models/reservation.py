import enum
import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.payment_transaction import PaymentTransaction
    from app.models.reservation_item import ReservationItem
    from app.models.ticket import Ticket


class ReservationStatus(str, enum.Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"  # panier abandonné : purge après TTL, jauge libérée


class SalesChannel(str, enum.Enum):
    """Canal de vente (DFC n°7) : web = billetterie en ligne (CB unique),
    pos = guichet physique / taverne (multi-paiements)."""

    WEB = "web"
    POS = "pos"


class Reservation(Base):
    __tablename__ = "reservations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    customer_email: Mapped[str] = mapped_column(String(320), nullable=False)
    total_price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    status: Mapped[ReservationStatus] = mapped_column(
        Enum(
            ReservationStatus,
            name="reservation_status",
            values_callable=lambda e: [m.value for m in e],
        ),
        default=ReservationStatus.PENDING,
        nullable=False,
    )
    sales_channel: Mapped[SalesChannel] = mapped_column(
        Enum(
            SalesChannel,
            name="sales_channel",
            values_callable=lambda e: [m.value for m in e],
        ),
        default=SalesChannel.WEB,
        server_default="web",
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    items: Mapped[list["ReservationItem"]] = relationship(
        back_populates="reservation", cascade="all, delete-orphan"
    )
    tickets: Mapped[list["Ticket"]] = relationship(
        back_populates="reservation", cascade="all, delete-orphan"
    )
    payments: Mapped[list["PaymentTransaction"]] = relationship(
        back_populates="reservation", cascade="all, delete-orphan"
    )

    @property
    def paid_amount(self) -> Decimal:
        return sum(
            (p.applied_amount for p in self.payments), Decimal(0)
        )

    @property
    def amount_due(self) -> Decimal:
        return max(self.total_price - self.paid_amount, Decimal(0))
