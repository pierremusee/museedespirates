import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.ticket import TicketScanRequest, TicketScanResponse
from app.services import ticket_service

router = APIRouter(tags=["tickets"])


@router.post("/tickets/{ticket_id}/scan", response_model=TicketScanResponse)
async def scan_ticket(
    ticket_id: uuid.UUID,
    checkpoint: TicketScanRequest,
    db: AsyncSession = Depends(get_db),
) -> TicketScanResponse:
    """Contrôle d'accès au poste choisi : `event_id` pour une entrée
    « journée » (musée), `session_id` pour une porte de séance."""
    return await ticket_service.scan_ticket(db, ticket_id, checkpoint)
