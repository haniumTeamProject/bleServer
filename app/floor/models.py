import uuid

from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Floor(Base):
    __tablename__ = "floors"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    building_id: Mapped[str] = mapped_column(String, nullable=False)
    floor: Mapped[int] = mapped_column(Integer, nullable=False)
    major: Mapped[int] = mapped_column(Integer, nullable=False)

    # floorplan_missing | review_needed | beacon_missing | connector_missing | ready
    status: Mapped[str] = mapped_column(String, default="floorplan_missing")
