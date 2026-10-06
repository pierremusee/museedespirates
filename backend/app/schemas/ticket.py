import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, model_validator

from app.models.reservation_item import FreeProfile
from app.models.ticket import TicketCategory, TicketType


class TicketAccessRead(BaseModel):
    """Un droit d'accès d'un billet (exposé dans les réponses)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    access_type: TicketType
    session_id: uuid.UUID | None
    event_id: uuid.UUID | None
    valid_date: date | None
    is_scanned: bool
    scanned_at: datetime | None
    session_start: datetime | None = None


class TicketScanRequest(BaseModel):
    """Poste de contrôle où le billet est présenté — exactement un
    des deux champs requis :

    - `event_id`   : poste « journée » (entrée du musée) — valide
                     l'accès `open_ticket` de cet événement.
    - `session_id` : poste « séance » (porte du théâtre) — valide
                     l'accès rattaché à cette séance précise.
    """

    event_id: uuid.UUID | None = None
    session_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def exactly_one_checkpoint(self) -> "TicketScanRequest":
        if (self.event_id is None) == (self.session_id is None):
            raise ValueError(
                "Fournir exactement un poste de contrôle : "
                "event_id (entrée journée) ou session_id (séance)"
            )
        return self


class TicketScanResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    access_type: TicketType
    session_id: uuid.UUID | None
    event_id: uuid.UUID | None
    valid_date: date | None
    is_scanned: bool
    scanned_at: datetime | None
    # Contexte de contrôle (idée n°6 — affichage agent)
    ticket_category: TicketCategory
    session_start: datetime | None = None
    free_profile: FreeProfile | None = None
    access_label: str = ""
    control_warning: str | None = None
    accesses: list[TicketAccessRead] = []
    message: str = "Billet valide — accès autorisé"
