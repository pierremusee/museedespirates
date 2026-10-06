import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.reservation_item import FreeProfile
from app.models.session import Session
from app.models.ticket import Ticket, TicketType
from app.models.ticket_access import TicketAccess
from app.schemas.ticket import (
    TicketAccessRead,
    TicketScanRequest,
    TicketScanResponse,
)

MUSEUM_TZ = ZoneInfo("Europe/Paris")

# Tolérance après le début de séance — au-delà, le billet est refusé.
LATE_TOLERANCE = timedelta(minutes=30)

FREE_WARNINGS = {
    FreeProfile.UNDER_4: "GRATUIT — Moins de 4 ans (vérifier l'âge)",
    FreeProfile.DISABILITY: "GRATUIT — Vérifier la carte d'invalidité",
    FreeProfile.PMR_COMPANION: "GRATUIT — Accompagnateur PMR",
}

ACCESS_LABELS = {
    TicketType.OPEN_TICKET: "Accès Musée — Billet Journée",
    TicketType.SESSION_STANDARD: "Accès Théâtre",
    TicketType.SESSION_DINING: "Accès Dîner-spectacle",
}


def _fr(dt: datetime) -> str:
    """HH:MM -> '15h30' à la française."""
    return dt.strftime("%Hh%M")


def _access_read(access: TicketAccess) -> TicketAccessRead:
    return TicketAccessRead(
        id=access.id,
        access_type=access.access_type,
        session_id=access.session_id,
        event_id=access.event_id,
        valid_date=access.valid_date,
        is_scanned=access.is_scanned,
        scanned_at=access.scanned_at,
        session_start=access.session_start,
    )


async def scan_ticket(
    db: AsyncSession, ticket_id: uuid.UUID, checkpoint: TicketScanRequest
) -> TicketScanResponse:
    """Contrôle d'accès branché sur `access_type` au poste choisi.

    Le billet (1 par personne) porte plusieurs droits consommés
    indépendamment ; le poste détermine lequel est validé :
    - `event_id`   : accès `open_ticket` de cet événement, valable le
      jour civil `valid_date` (Europe/Paris), sans contrainte d'heure.
    - `session_id` : accès de cette séance, valable le jour de la
      séance ET jusqu'à `start_time + 30 min`.
    - Dans tous les cas : usage unique par accès (`is_scanned`) et
      remontée d'une alerte pour les billets gratuits (`free_profile`).
    """
    result = await db.execute(
        select(Ticket)
        .options(selectinload(Ticket.item))
        .where(Ticket.id == ticket_id)
    )
    ticket = result.scalar_one_or_none()

    if ticket is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Billet introuvable",
        )

    # Verrou anti double-scan : deux douchettes simultanées sur le même
    # billet se sérialisent ici — la seconde voit l'accès déjà consommé.
    result = await db.execute(
        select(TicketAccess)
        .options(
            selectinload(TicketAccess.session).selectinload(Session.event)
        )
        .where(TicketAccess.ticket_id == ticket.id)
        .with_for_update()
    )
    accesses = result.scalars().all()

    def _matches_checkpoint(a: TicketAccess) -> bool:
        if checkpoint.session_id is not None:
            return a.session_id == checkpoint.session_id
        return (
            a.access_type == TicketType.OPEN_TICKET
            and a.event_id == checkpoint.event_id
        )

    access = next(
        (a for a in accesses if _matches_checkpoint(a) and not a.is_scanned),
        None,
    )

    if access is None:
        used = next(
            (a for a in accesses if _matches_checkpoint(a) and a.is_scanned),
            None,
        )
        if used is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Accès déjà utilisé le "
                f"{used.scanned_at.astimezone(MUSEUM_TZ).strftime('%d/%m/%Y à %H:%M')}"
                if used.scanned_at
                else "Accès déjà utilisé",
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ce billet ne donne pas droit à cette entrée",
        )

    now = datetime.now(timezone.utc)
    now_paris = now.astimezone(MUSEUM_TZ)
    today = now_paris.date()

    access_label = ACCESS_LABELS[access.access_type]
    session_start: datetime | None = None

    if access.access_type == TicketType.OPEN_TICKET:
        if access.valid_date != today:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Billet Musée valable le "
                f"{access.valid_date.strftime('%d/%m/%Y')} — "
                f"nous sommes le {today.strftime('%d/%m/%Y')}",
            )
    else:
        session = access.session
        if session is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Accès sans séance associée",
            )
        start_paris = session.start_time.astimezone(MUSEUM_TZ)
        session_start = session.start_time
        access_label = (
            f"{access_label} — {session.event.title}"
            f" — Séance de {_fr(start_paris)}"
        )

        if start_paris.date() != today:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Billet valable pour la séance du "
                f"{start_paris.strftime('%d/%m/%Y')} à {_fr(start_paris)} — "
                f"pas aujourd'hui",
            )
        if now_paris > start_paris + LATE_TOLERANCE:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Séance de {_fr(start_paris)} passée depuis plus de "
                "30 min — billet non valable",
            )

    free_profile = ticket.item.free_profile if ticket.item else None
    control_warning = FREE_WARNINGS.get(free_profile)

    access.is_scanned = True
    access.scanned_at = now
    await db.commit()

    return TicketScanResponse(
        id=ticket.id,
        access_type=access.access_type,
        session_id=access.session_id,
        event_id=access.event_id,
        valid_date=access.valid_date,
        is_scanned=access.is_scanned,
        scanned_at=access.scanned_at,
        ticket_category=ticket.ticket_category,
        session_start=session_start,
        free_profile=free_profile,
        access_label=access_label,
        control_warning=control_warning,
        accesses=[_access_read(a) for a in accesses],
        message=access_label,
    )
