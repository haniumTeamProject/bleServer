import uuid

from sqlalchemy import Float, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Landmark(Base):
    __tablename__ = "landmarks"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    floor_id: Mapped[str] = mapped_column(String, nullable=False)

    # 사용자가 음성으로 말하는 목적지 이름
    name: Mapped[str | None] = mapped_column(String, nullable=True)

    # room | restroom | facility | entrance
    type: Mapped[str | None] = mapped_column(String, nullable=True)

    visual_tag_id: Mapped[str | None] = mapped_column(String, nullable=True)

    x: Mapped[float | None] = mapped_column(Float, nullable=True)
    y: Mapped[float | None] = mapped_column(Float, nullable=True)
