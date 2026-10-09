import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.payment_transaction import PaymentMethod, PaymentStatus
from app.models.reservation import ReservationStatus, SalesChannel
from app.models.reservation_item import FreeProfile
from app.models.ticket import TicketCategory
from app.schemas.ticket import TicketAccessRead

# Bornes d'entrée (anti-débordement, anti-DoS) — limites métier, pas
# techniques arbitraires :
# - une commande couvre au plus MAX_PERSONS_PER_RESERVATION personnes
#   (repères : jauge de séance seedée ~80, « capacité de 120 » dans la
#   spec — OBJECTIFS.md §17) — chaque personne émet des billets et les
#   produits sans jauge n'ont pas d'autre borne ;
# - une commande compte au plus MAX_ITEMS_PER_RESERVATION lignes :
#   les interfaces émettent 1 ligne par personne individuelle, donc
#   la borne des lignes ne peut pas être inférieure au plafond de
#   personnes — 120 lignes = 120 billets individuels vendables ;
# - une tranche d'encaissement est plafonnée à 50 000 €, sous la
#   capacité Numeric(10,2) = 99 999 999,99 € — aucun dépassement
#   PostgreSQL possible.
# Les bornes par champ échouent en 422 (Pydantic) ; le total de
# personnes est aussi re-vérifié en agrégé par reservation_service,
# car des lignes individuellement valides peuvent le dépasser.
MAX_ITEMS_PER_RESERVATION = 120
MAX_PERSONS_PER_RESERVATION = 120
MAX_PAYMENT_AMOUNT = Decimal("50000.00")


class ReservationItemCreate(BaseModel):
    """Une ligne de panier : un produit du catalogue + ses paramètres.

    - `visit_date`   : requis pour tout produit contenant un accès Musée
                       (open_ticket). Doit coïncider avec le jour des
                       séances jointes (règle du Pass « Journée »).
    - `session_id(s)`: requis pour tout produit contenant des séances
                       (session_ticket), en nombre exact.
    - `free_profile` : profil de gratuité (DFC n°6) — l'item est émis
                       à 0 € et les tickets gardent leur catégorie
                       physique (jauge conservée).
    - `group_size`   : obligatoire pour les produits `kind = group`,
                       minimum 8 personnes.
    """

    product_code: str
    category: TicketCategory | None = None
    visit_date: date | None = None
    session_id: uuid.UUID | None = None
    session_ids: list[uuid.UUID] = Field(default_factory=list)
    extra_children: int = Field(
        default=0, ge=0, le=MAX_PERSONS_PER_RESERVATION
    )
    group_size: int | None = Field(
        default=None, ge=1, le=MAX_PERSONS_PER_RESERVATION
    )
    free_profile: FreeProfile | None = None


class ReservationCreate(BaseModel):
    # EmailStr borne déjà l'adresse à 254 car. (RFC 5321) — sous la
    # capacité de la colonne String(320) : aucun dépassement possible.
    customer_email: EmailStr
    channel: SalesChannel = SalesChannel.WEB
    items: list[ReservationItemCreate] = Field(
        min_length=1, max_length=MAX_ITEMS_PER_RESERVATION
    )


class PaymentCreate(BaseModel):
    """Une tranche d'encaissement (DFC n°7).

    `amount` = montant nominal remis par le client. Pour les espèces et
    les chèques-vacances, il peut excéder le reste à payer (rendu pour
    le cash, excédent perdu pour l'ANCV).

    `idempotency_key` (obligatoire) identifie l'opération logique :
    générée par le client (UUID v4) au début de chaque nouvel
    encaissement, réutilisée telle quelle pour rejouer la même opération
    après une réponse incertaine (timeout, coupure réseau). Une même
    clé avec un contenu différent est rejetée (409).
    """

    method: PaymentMethod
    amount: Decimal = Field(
        gt=0, le=MAX_PAYMENT_AMOUNT, decimal_places=2
    )
    idempotency_key: uuid.UUID


class PaymentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    method: PaymentMethod
    amount: Decimal
    applied_amount: Decimal
    status: PaymentStatus
    created_at: datetime


class TicketRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    item_id: uuid.UUID | None
    ticket_category: TicketCategory
    accesses: list[TicketAccessRead]


class ReservationItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    product_code: str
    category: TicketCategory | None
    extra_children: int
    group_size: int | None
    free_profile: FreeProfile | None
    visit_date: date | None
    computed_price: Decimal
    season_modifier: Decimal
    tickets: list[TicketRead]


class ReservationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    customer_email: str
    total_price: Decimal
    status: ReservationStatus
    sales_channel: SalesChannel
    paid_amount: Decimal
    amount_due: Decimal
    created_at: datetime
    items: list[ReservationItemRead]
    tickets: list[TicketRead]
    payments: list[PaymentRead]


class PaymentResult(BaseModel):
    """Réponse de l'encaissement d'une tranche (DFC n°7)."""

    payment: PaymentRead
    change_due: Decimal
    amount_due: Decimal
    reservation_status: ReservationStatus
    tickets: list[TicketRead]
