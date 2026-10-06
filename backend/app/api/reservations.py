import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.session import get_db
from app.models.reservation import Reservation
from app.models.reservation_item import ReservationItem
from app.models.session import Session
from app.models.ticket import Ticket
from app.models.ticket_access import TicketAccess
from app.schemas.reservation import (
    PaymentCreate,
    PaymentResult,
    ReservationCreate,
    ReservationRead,
)
from app.services import reservation_service

router = APIRouter(tags=["reservations"])


def _reservation_options():
    return (
        selectinload(Reservation.items)
        .selectinload(ReservationItem.tickets)
        .selectinload(Ticket.accesses)
        .selectinload(TicketAccess.session)
        .selectinload(Session.event),
        selectinload(Reservation.items).selectinload(ReservationItem.product),
        selectinload(Reservation.tickets)
        .selectinload(Ticket.accesses)
        .selectinload(TicketAccess.session)
        .selectinload(Session.event),
        selectinload(Reservation.payments),
    )


@router.post(
    "/reservations",
    response_model=ReservationRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_reservation(
    data: ReservationCreate, db: AsyncSession = Depends(get_db)
) -> ReservationRead:
    return await reservation_service.create_reservation(db, data)


@router.get("/reservations/{reservation_id}", response_model=ReservationRead)
async def get_reservation(
    reservation_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> ReservationRead:
    result = await db.execute(
        select(Reservation)
        .options(*_reservation_options())
        .where(Reservation.id == reservation_id)
    )
    reservation = result.scalar_one_or_none()
    if reservation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Réservation introuvable",
        )
    return reservation


@router.post(
    "/reservations/{reservation_id}/cancel",
    response_model=ReservationRead,
)
async def cancel_reservation(
    reservation_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> ReservationRead:
    """Annule une commande en attente (caisse : client change d'avis).

    Les sièges réservés sont restitués ; les encaissements éventuels
    restent enregistrés (remboursement manuel à la caisse).
    """
    return await reservation_service.cancel_reservation(db, reservation_id)


@router.post(
    "/reservations/{reservation_id}/payments",
    response_model=PaymentResult,
    status_code=status.HTTP_201_CREATED,
)
async def pay_reservation(
    reservation_id: uuid.UUID,
    data: PaymentCreate,
    db: AsyncSession = Depends(get_db),
) -> PaymentResult:
    """Encaisse une tranche de paiement (DFC n°7).

    Canal `web` : CB unique du montant total. Canal `pos` : multi-
    paiements (cb, cash, ancv, check) ; rendu de monnaie sur les
    espèces uniquement, jamais sur les chèques-vacances.
    """
    payment, change_due, amount_due, reservation = (
        await reservation_service.add_payment(db, reservation_id, data)
    )
    return PaymentResult(
        payment=payment,
        change_due=change_due,
        amount_due=amount_due,
        reservation_status=reservation.status,
        tickets=reservation.tickets,
    )
