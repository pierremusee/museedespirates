import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.services import reservation_service

router = APIRouter(prefix="/admin", tags=["admin"])


class PurgeResult(BaseModel):
    purged: int
    reservation_ids: list[uuid.UUID]


@router.post("/reservations/purge", response_model=PurgeResult)
async def purge_expired_reservations(
    db: AsyncSession = Depends(get_db),
) -> PurgeResult:
    """Expire les réservations `pending` de plus de 15 minutes et
    restitue les sièges bloqués (`booked_seats` décrémenté).

    Endpoint de maintenance : appelé aussi par la tâche de fond du
    serveur — peut être raccordé à un cron externe.
    """
    ids = await reservation_service.purge_expired_reservations(db)
    return PurgeResult(purged=len(ids), reservation_ids=ids)
