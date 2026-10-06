import enum
import uuid
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Enum, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.event import Event
    from app.models.reservation_item import ReservationItem


class ProductKind(str, enum.Enum):
    SIMPLE = "simple"
    PASS = "pass"
    FAMILY = "family"
    GROUP = "group"  # vente groupe : minimum 8 personnes


class ComponentType(str, enum.Enum):
    MUSEUM_DAY = "museum_day"
    THEATER_SESSION = "theater_session"
    DINING_SESSION = "dining_session"


class Product(Base):
    """Produit vendable du catalogue (billet simple, pass, forfait famille).

    Les prix portés ici sont les prix de base « basse saison » ; le
    modificateur haute saison est appliqué dynamiquement au calcul,
    jamais stocké en base.
    """

    __tablename__ = "products"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[ProductKind] = mapped_column(
        Enum(ProductKind, name="product_kind", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    )
    price_adult: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    price_child: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    price_reduced: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    family_base_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    extra_child_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    # Produit « add-on » (ex. séance supplémentaire à tarif réduit) :
    # ne peut être vendu qu'en complément de produits couvrant déjà les
    # mêmes droits dans la même commande — sinon il court-circuiterait
    # les pass (musée + séance supp. < Pass 1 Spectacle).
    is_addon: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)

    components: Mapped[list["ProductComponent"]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )
    items: Mapped[list["ReservationItem"]] = relationship(back_populates="product")


class ProductComponent(Base):
    """Contenu d'un produit, PAR PERSONNE (ex: pass_1_show = 1 museum_day + 1 theater_session).

    `event_id` est renseigné pour les composants `museum_day` (l'événement
    musée que le billet ouvre) et laissé nul pour les composants liés à
    une séance, l'événement étant résolu au moment de la réservation via
    `session_id`.
    """

    __tablename__ = "product_components"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id"), nullable=False, index=True
    )
    component_type: Mapped[ComponentType] = mapped_column(
        Enum(ComponentType, name="component_type", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    event_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("events.id"), nullable=True
    )

    product: Mapped["Product"] = relationship(back_populates="components")
    event: Mapped["Event | None"] = relationship()
