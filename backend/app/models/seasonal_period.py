import uuid
from datetime import date

from sqlalchemy import Date, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SeasonalPeriod(Base):
    """Plage de dates qualifiées de « Haute Saison ».

    La présence d'une date dans une période déclenche le modificateur
    tarifaire haute saison au calcul du panier (DFC n°5 §4).
    """

    __tablename__ = "seasonal_periods"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
