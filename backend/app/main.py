import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import admin, events, products, reservations, tickets
from app.db.session import AsyncSessionLocal
from app.services import reservation_service

logger = logging.getLogger(__name__)

PURGE_INTERVAL_S = 60


async def _purge_loop() -> None:
    """Purge périodique des paniers abandonnés (> 15 min) : libère les
    sièges réservés par des commandes jamais soldées."""
    while True:
        await asyncio.sleep(PURGE_INTERVAL_S)
        try:
            async with AsyncSessionLocal() as db:
                ids = await reservation_service.purge_expired_reservations(db)
            if ids:
                logger.info("Purge paniers : %d réservation(s) expirée(s)", len(ids))
        except Exception:
            logger.exception("Échec de la purge des réservations expirées")


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_purge_loop())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(
    title="Musée des Pirates",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(admin.router)
app.include_router(events.router)
app.include_router(products.router)
app.include_router(reservations.router)
app.include_router(tickets.router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "project": "Musée des Pirates"}
